"""Project hierarchy tree widget with hide/unhide support."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAction,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.models import DiffSummary, ProjectInfo

# Custom data roles
ROLE_PROJECT_NAME = 256  # Qt.UserRole
ROLE_ORIGINAL_LABEL = 257


class ProjectTree(QWidget):
    """Widget showing Citect project hierarchy with hide/unhide support.

    Contains a QTreeWidget and a toggle label for hidden projects.
    Top-level items are projects not included by any other project.
    Child items are their includes, shown recursively.
    Projects without local folders are shown grayed out.
    Unchecked projects are excluded from snapshots and diffs.
    """

    project_filter_changed = pyqtSignal(object)  # set[str] or None for all
    exclusions_changed = pyqtSignal(set)  # set of excluded project names
    hidden_changed = pyqtSignal(set)  # set of hidden project names
    view_mode_changed = pyqtSignal(bool)  # True = flat mode

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # View mode toggle
        self._view_bar = QWidget()
        view_layout = QHBoxLayout(self._view_bar)
        view_layout.setContentsMargins(4, 2, 4, 2)
        view_layout.addWidget(QLabel("Projects"))
        view_layout.addStretch()
        self._view_toggle = QPushButton("Flat View")
        self._view_toggle.setFixedHeight(22)
        self._view_toggle.setStyleSheet("font-size: 11px; padding: 0 8px;")
        self._view_toggle.clicked.connect(self._toggle_view_mode)
        view_layout.addWidget(self._view_toggle)
        layout.addWidget(self._view_bar)

        # Tree widget
        self._flat_mode = False
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        tree_sel = self.tree.selectionModel()
        assert tree_sel is not None
        tree_sel.selectionChanged.connect(self._on_selection_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree)

        # Hidden projects bar
        self._hidden_bar = QWidget()
        hidden_layout = QHBoxLayout(self._hidden_bar)
        hidden_layout.setContentsMargins(4, 2, 4, 2)
        self._hidden_label = QLabel()
        self._hidden_label.setStyleSheet("color: #888; font-size: 12px;")
        hidden_layout.addWidget(self._hidden_label)
        hidden_layout.addStretch()
        self._toggle_btn = QPushButton("Show")
        self._toggle_btn.setFixedHeight(22)
        self._toggle_btn.setStyleSheet("font-size: 11px; padding: 0 8px;")
        self._toggle_btn.clicked.connect(self._toggle_show_hidden)
        hidden_layout.addWidget(self._toggle_btn)
        self._hidden_bar.setVisible(False)
        layout.addWidget(self._hidden_bar)

        self._projects: dict[str, ProjectInfo] = {}
        self._all_item: QTreeWidgetItem | None = None
        self._change_counts: dict[str, int] = {}
        self._excluded: set[str] = set()
        self._hidden: set[str] = set()
        self._show_hidden = False
        self._updating = False  # Prevent recursive signals

    def set_projects(self, projects: dict[str, ProjectInfo]) -> None:
        """Populate the tree from project discovery results."""
        self._projects = projects
        self._change_counts = {}

        # Clean up exclusions/hidden to only include valid project names
        self._excluded = self._excluded & set(projects.keys())
        self._hidden = self._hidden & set(projects.keys())

        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        """Rebuild the tree widget in the current view mode."""
        self.tree.blockSignals(True)
        self._updating = True

        try:
            self.tree.clear()

            # "All Projects" root item
            self._all_item = QTreeWidgetItem(self.tree, ["All Projects"])
            self._all_item.setData(0, ROLE_PROJECT_NAME, "")
            self._all_item.setData(0, ROLE_ORIGINAL_LABEL, "All Projects")
            self._all_item.setExpanded(True)
            self._all_item.setFlags(
                self._all_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            self._all_item.setCheckState(0, Qt.CheckState.Checked)

            if self._flat_mode:
                self._populate_flat()
            else:
                self._populate_tree()

            # Update "All Projects" check state based on children
            self._update_parent_check_state_no_recurse(self._all_item)

            # Apply hidden visibility
            self._apply_hidden_visibility()

            # Re-apply change indicators if we have them
            if self._change_counts:
                self._apply_change_indicators()
        finally:
            self._updating = False
            self.tree.blockSignals(False)

    def _populate_tree(self) -> None:
        """Populate as hierarchical tree view."""
        assert self._all_item is not None
        self.tree.setRootIsDecorated(True)

        included_by_others: set[str] = set()
        for p in self._projects.values():
            for inc in p.includes:
                included_by_others.add(inc)

        top_level = sorted(
            [p for p in self._projects.values() if p.name not in included_by_others],
            key=lambda p: p.name,
        )

        for project in top_level:
            item = self._add_project_item(self._all_item, project, set())
            item.setExpanded(True)

    def _populate_flat(self) -> None:
        """Populate as flat alphabetical list."""
        assert self._all_item is not None
        self.tree.setRootIsDecorated(False)

        for project in sorted(self._projects.values(), key=lambda p: p.name):
            self._add_flat_item(self._all_item, project)

    def _add_flat_item(
        self, parent_item: QTreeWidgetItem, project: ProjectInfo
    ) -> QTreeWidgetItem:
        """Add a single project item with no children."""
        has_folder = Path(project.local_path).is_dir()
        label = project.name
        if project.title:
            label = f"{project.name} - {project.title}"

        item = QTreeWidgetItem(parent_item, [label])
        item.setData(0, ROLE_PROJECT_NAME, project.name)
        item.setData(0, ROLE_ORIGINAL_LABEL, label)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        if project.name in self._excluded:
            item.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            item.setCheckState(0, Qt.CheckState.Checked)

        if not has_folder:
            item.setForeground(0, QColor(150, 150, 150))
            item.setToolTip(0, "Project folder not found locally")

        return item

    def _toggle_view_mode(self) -> None:
        """Switch between tree and flat view."""
        self.set_flat_mode(not self._flat_mode)
        self.view_mode_changed.emit(self._flat_mode)

    def set_flat_mode(self, flat: bool) -> None:
        """Set the view mode without emitting a signal."""
        self._flat_mode = flat
        self._view_toggle.setText("Tree View" if self._flat_mode else "Flat View")
        if self._projects:
            self._rebuild_tree()

    def _add_project_item(
        self,
        parent_item: QTreeWidgetItem,
        project: ProjectInfo,
        visited: set[str],
    ) -> QTreeWidgetItem:
        """Recursively add a project and its includes to the tree."""
        has_folder = Path(project.local_path).is_dir()
        label = project.name
        if project.title:
            label = f"{project.name} - {project.title}"

        item = QTreeWidgetItem(parent_item, [label])
        item.setData(0, ROLE_PROJECT_NAME, project.name)
        item.setData(0, ROLE_ORIGINAL_LABEL, label)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        # Restore exclusion state
        if project.name in self._excluded:
            item.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            item.setCheckState(0, Qt.CheckState.Checked)

        if not has_folder:
            item.setForeground(0, QColor(150, 150, 150))
            item.setToolTip(0, "Project folder not found locally")

        visited.add(project.name)

        for inc_name in project.includes:
            if inc_name in visited:
                # Show but don't recurse (cycle)
                cycle_item = QTreeWidgetItem(item, [f"{inc_name} (circular ref)"])
                cycle_item.setForeground(0, QColor(180, 180, 180))
                cycle_item.setData(0, ROLE_PROJECT_NAME, inc_name)
                continue

            if inc_name in self._projects:
                self._add_project_item(
                    item, self._projects[inc_name], visited.copy()
                )
            else:
                # Project in include but not in MASTER.DBF
                missing = QTreeWidgetItem(item, [f"{inc_name} (not in master)"])
                missing.setForeground(0, QColor(180, 180, 180))
                missing.setData(0, ROLE_PROJECT_NAME, inc_name)

        return item

    # -- Hide / Unhide --

    def _show_context_menu(self, position) -> None:
        """Show right-click context menu for hide/unhide."""
        item = self.tree.itemAt(position)
        if item is None:
            return

        # Collect project names from all selected items
        selected_items = self.tree.selectedItems()
        selected_names = set()
        for sel in selected_items:
            name = sel.data(0, ROLE_PROJECT_NAME)
            if name:  # skip "All Projects" (empty string)
                selected_names.add(name)

        menu = QMenu(self)

        # If right-clicked on "All Projects" or no real projects selected
        if not selected_names:
            if self._hidden:
                unhide_all = QAction(
                    f"Unhide all ({len(self._hidden)} hidden)", self
                )
                unhide_all.triggered.connect(self._unhide_all)
                menu.addAction(unhide_all)
                tree_vp = self.tree.viewport()
                if tree_vp is not None:
                    menu.exec_(tree_vp.mapToGlobal(position))
            return

        # Determine which are hidden and which are visible
        to_hide = selected_names - self._hidden
        to_unhide = selected_names & self._hidden

        if to_hide:
            count = len(to_hide)
            label = (
                f"Hide '{next(iter(to_hide))}'"
                if count == 1
                else f"Hide {count} projects"
            )
            hide_action = QAction(label, self)
            names = to_hide.copy()
            hide_action.triggered.connect(lambda: self._hide_projects(names))
            menu.addAction(hide_action)

        if to_unhide:
            count = len(to_unhide)
            label = (
                f"Unhide '{next(iter(to_unhide))}'"
                if count == 1
                else f"Unhide {count} projects"
            )
            unhide_action = QAction(label, self)
            names = to_unhide.copy()
            unhide_action.triggered.connect(lambda: self._unhide_projects(names))
            menu.addAction(unhide_action)

        if self._hidden:
            menu.addSeparator()
            unhide_all = QAction(
                f"Unhide all ({len(self._hidden)} hidden)", self
            )
            unhide_all.triggered.connect(self._unhide_all)
            menu.addAction(unhide_all)

        tree_vp = self.tree.viewport()
        if tree_vp is not None:
            menu.exec_(tree_vp.mapToGlobal(position))

    def _hide_projects(self, names: set[str]) -> None:
        """Hide one or more projects from the tree."""
        self._hidden |= names
        self._apply_hidden_visibility()
        self.hidden_changed.emit(self._hidden.copy())

    def _unhide_projects(self, names: set[str]) -> None:
        """Unhide one or more projects in the tree."""
        self._hidden -= names
        self._apply_hidden_visibility()
        self.hidden_changed.emit(self._hidden.copy())

    def _unhide_all(self) -> None:
        """Unhide all projects."""
        self._hidden.clear()
        self._show_hidden = False
        self._apply_hidden_visibility()
        self.hidden_changed.emit(self._hidden.copy())

    def _toggle_show_hidden(self) -> None:
        """Toggle visibility of hidden projects."""
        self._show_hidden = not self._show_hidden
        self._apply_hidden_visibility()

    def _apply_hidden_visibility(self) -> None:
        """Apply hidden state to all tree items."""
        if not self._all_item:
            return

        self.tree.blockSignals(True)
        self._updating = True
        try:
            self._apply_hidden_recursive(self._all_item)
        finally:
            self._updating = False
            self.tree.blockSignals(False)

        # Update hidden bar
        count = len(self._hidden)
        if count > 0:
            self._hidden_label.setText(f"{count} project(s) hidden")
            self._toggle_btn.setText(
                "Hide" if self._show_hidden else "Show"
            )
            self._hidden_bar.setVisible(True)
        else:
            self._hidden_bar.setVisible(False)

    def _apply_hidden_recursive(self, parent: QTreeWidgetItem) -> None:
        """Recursively apply hidden/visible state to tree items."""
        for i in range(parent.childCount()):
            item = parent.child(i)
            if item is None:
                continue
            project_name = item.data(0, ROLE_PROJECT_NAME)

            if project_name and project_name in self._hidden:
                if self._show_hidden:
                    # Show but styled differently
                    item.setHidden(False)
                    font = item.font(0)
                    font.setItalic(True)
                    item.setFont(0, font)
                    item.setForeground(0, QColor(150, 150, 150))
                else:
                    item.setHidden(True)
            else:
                item.setHidden(False)
                # Restore normal style (italic off)
                font = item.font(0)
                font.setItalic(False)
                item.setFont(0, font)

            self._apply_hidden_recursive(item)

    def set_hidden_projects(self, hidden: set[str]) -> None:
        """Set the hidden projects (e.g. restored from settings)."""
        self._hidden = hidden.copy()
        self._apply_hidden_visibility()

    # -- Checkbox handling --

    def _on_selection_changed(self) -> None:
        """Handle tree selection changes - collect all selected projects."""
        selected_items = self.tree.selectedItems()
        if not selected_items:
            self.project_filter_changed.emit(None)
            return

        # If "All Projects" is among the selected items, no filter
        for item in selected_items:
            if item.data(0, ROLE_PROJECT_NAME) == "":
                self.project_filter_changed.emit(None)
                return

        # Collect selected projects (in flat mode, no descendants)
        combined: set[str] = set()
        for item in selected_items:
            project_name = item.data(0, ROLE_PROJECT_NAME)
            if project_name:
                if self._flat_mode:
                    combined.add(project_name)
                else:
                    self._collect_descendants(project_name, combined)

        self.project_filter_changed.emit(combined if combined else None)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle checkbox state changes."""
        if self._updating:
            return

        project_name = item.data(0, ROLE_PROJECT_NAME)
        if project_name is None:
            return

        # Block signals to prevent recursive updates
        self.tree.blockSignals(True)
        self._updating = True

        try:
            # Handle "All Projects" toggle
            if project_name == "" and item == self._all_item:
                checked = item.checkState(0) == Qt.CheckState.Checked
                self._set_all_children_checked(item, checked)
                if checked:
                    self._excluded.clear()
                else:
                    self._excluded = set(self._projects.keys())
            else:
                # Individual project toggle
                checked = item.checkState(0) == Qt.CheckState.Checked
                if checked:
                    self._excluded.discard(project_name)
                else:
                    self._excluded.add(project_name)

                # If this item has children, toggle them too
                if item.childCount() > 0:
                    self._set_all_children_checked(item, checked)
                    self._update_children_exclusions(item, checked)

                # Update parent check states
                self._update_parent_check_state(item)
        finally:
            self._updating = False
            self.tree.blockSignals(False)

        self.exclusions_changed.emit(self._excluded.copy())

    def _set_all_children_checked(
        self, parent: QTreeWidgetItem, checked: bool
    ) -> None:
        """Recursively set check state for all children."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child is None:
                continue
            child.setCheckState(0, state)
            self._set_all_children_checked(child, checked)

    def _update_children_exclusions(
        self, parent: QTreeWidgetItem, checked: bool
    ) -> None:
        """Recursively update exclusions for all children."""
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child is None:
                continue
            project_name = child.data(0, ROLE_PROJECT_NAME)
            if project_name:
                if checked:
                    self._excluded.discard(project_name)
                else:
                    self._excluded.add(project_name)
            self._update_children_exclusions(child, checked)

    def _update_parent_check_state(self, item: QTreeWidgetItem) -> None:
        """Update parent check state based on children."""
        parent = item.parent()
        if parent is None:
            return

        # Count checked and partially checked children
        checked_count = 0
        partial_count = 0
        for i in range(parent.childCount()):
            ch = parent.child(i)
            if ch is None:
                continue
            state = ch.checkState(0)
            if state == Qt.CheckState.Checked:
                checked_count += 1
            elif state == Qt.CheckState.PartiallyChecked:
                partial_count += 1

        # Set parent state
        if checked_count == parent.childCount():
            parent.setCheckState(0, Qt.CheckState.Checked)
        elif checked_count == 0 and partial_count == 0:
            parent.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            parent.setCheckState(0, Qt.CheckState.PartiallyChecked)

        # Recurse up
        self._update_parent_check_state(parent)

    def get_excluded_projects(self) -> set[str]:
        """Return the set of excluded project names."""
        return self._excluded.copy()

    def get_project_with_descendants(self, project_name: str) -> set[str]:
        """Return a set containing the project and all its descendants.

        If project_name is empty (All Projects), returns an empty set.
        """
        if not project_name:
            return set()

        result: set[str] = set()
        self._collect_descendants(project_name, result)
        return result

    def _collect_descendants(self, project_name: str, result: set[str]) -> None:
        """Recursively collect a project and all its descendants."""
        if project_name in result:
            return  # Avoid cycles
        result.add(project_name)

        if project_name in self._projects:
            for child_name in self._projects[project_name].includes:
                self._collect_descendants(child_name, result)

    def set_excluded_projects(self, excluded: set[str]) -> None:
        """Set the excluded projects and update checkboxes."""
        self._excluded = excluded.copy()
        if self._all_item:
            self.tree.blockSignals(True)
            self._updating = True
            try:
                self._restore_check_states(self._all_item)
            finally:
                self._updating = False
                self.tree.blockSignals(False)

    def _restore_check_states(self, parent: QTreeWidgetItem) -> None:
        """Recursively restore check states from exclusion set."""
        for i in range(parent.childCount()):
            item = parent.child(i)
            if item is None:
                continue
            project_name = item.data(0, ROLE_PROJECT_NAME)
            if project_name:
                if project_name in self._excluded:
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    item.setCheckState(0, Qt.CheckState.Checked)
            self._restore_check_states(item)

        # Update this parent's state
        self._update_parent_check_state_no_recurse(parent)

    def _update_parent_check_state_no_recurse(
        self, parent: QTreeWidgetItem
    ) -> None:
        """Update parent check state without recursing up."""
        if parent.childCount() == 0:
            return
        checked_count = 0
        for i in range(parent.childCount()):
            ch = parent.child(i)
            if ch is None:
                continue
            state = ch.checkState(0)
            if state == Qt.CheckState.Checked:
                checked_count += 1
            elif state == Qt.CheckState.PartiallyChecked:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
                return

        if checked_count == 0:
            parent.setCheckState(0, Qt.CheckState.Unchecked)
        elif checked_count == parent.childCount():
            parent.setCheckState(0, Qt.CheckState.Checked)
        else:
            parent.setCheckState(0, Qt.CheckState.PartiallyChecked)

    def update_change_indicators(self, diff_summary: Optional[DiffSummary]) -> None:
        """Update project items to show change counts from a diff."""
        self._change_counts = {}
        if diff_summary:
            for project_name, tables in diff_summary.changes_by_project.items():
                count = sum(len(diffs) for diffs in tables.values())
                if count > 0:
                    self._change_counts[project_name] = count

        self._apply_change_indicators()

    def _apply_change_indicators(self) -> None:
        """Apply stored change counts to tree item labels."""
        self.tree.blockSignals(True)
        self._updating = True

        try:
            if self._all_item:
                self._update_item_labels(self._all_item)
                # All Projects shows the true deduplicated total
                total = sum(self._change_counts.values())
                original = self._all_item.data(0, ROLE_ORIGINAL_LABEL) or "All Projects"
                if total > 0:
                    self._all_item.setText(0, f"{original} ({total})")
                    font = self._all_item.font(0)
                    font.setBold(True)
                    self._all_item.setFont(0, font)
                else:
                    self._all_item.setText(0, original)
                    font = self._all_item.font(0)
                    font.setBold(False)
                    self._all_item.setFont(0, font)
        finally:
            self._updating = False
            self.tree.blockSignals(False)

    def _collect_unique_descendant_names(
        self, item: QTreeWidgetItem
    ) -> set[str]:
        """Collect all unique project names in an item's subtree."""
        names: set[str] = set()
        for i in range(item.childCount()):
            child = item.child(i)
            if child is None:
                continue
            name = child.data(0, ROLE_PROJECT_NAME)
            if name:
                names.add(name)
            names |= self._collect_unique_descendant_names(child)
        return names

    def _update_item_labels(self, parent: QTreeWidgetItem) -> None:
        """Recursively update item labels with own + included change counts."""
        for i in range(parent.childCount()):
            item = parent.child(i)
            if item is None:
                continue
            project_name = item.data(0, ROLE_PROJECT_NAME)
            original_label = item.data(0, ROLE_ORIGINAL_LABEL)

            # Recurse first so children are updated
            self._update_item_labels(item)

            if not project_name or not original_label:
                continue

            # Own changes for this project
            own_count = self._change_counts.get(project_name, 0)

            # Deduplicated included changes from unique descendant projects
            descendant_names = self._collect_unique_descendant_names(item)
            descendant_names.discard(project_name)
            included_count = sum(
                self._change_counts.get(n, 0) for n in descendant_names
            )

            # Format label
            if own_count > 0 and included_count > 0:
                label = f"{original_label} ({own_count} + {included_count} incl.)"
            elif own_count > 0:
                label = f"{original_label} ({own_count})"
            elif included_count > 0:
                label = f"{original_label} ({included_count} incl.)"
            else:
                label = original_label

            item.setText(0, label)

            has_changes = own_count > 0 or included_count > 0
            font = item.font(0)
            font.setBold(has_changes)
            item.setFont(0, font)

            if has_changes:
                item.setForeground(0, QColor(0, 100, 180))
            elif project_name in self._projects:
                has_folder = Path(
                    self._projects[project_name].local_path
                ).is_dir()
                if not has_folder:
                    item.setForeground(0, QColor(150, 150, 150))
                else:
                    item.setData(0, Qt.ItemDataRole.ForegroundRole, None)

    def clear_change_indicators(self) -> None:
        """Remove all change indicators."""
        self.update_change_indicators(None)
