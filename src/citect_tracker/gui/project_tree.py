"""Project hierarchy tree widget."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem

from ..core.models import DiffSummary, ProjectInfo

# Custom data roles
ROLE_PROJECT_NAME = 256  # Qt.UserRole
ROLE_ORIGINAL_LABEL = 257


class ProjectTree(QTreeWidget):
    """Tree widget showing Citect project hierarchy.

    Top-level items are projects not included by any other project.
    Child items are their includes, shown recursively.
    Projects without local folders are shown grayed out.
    Unchecked projects are excluded from snapshots and diffs.
    """

    project_selected = pyqtSignal(str)  # project name or "" for all
    project_deselected = pyqtSignal()
    exclusions_changed = pyqtSignal(set)  # set of excluded project names

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabel("Projects")
        self.setRootIsDecorated(True)
        self.itemClicked.connect(self._on_item_clicked)
        self.itemChanged.connect(self._on_item_changed)
        self._projects: dict[str, ProjectInfo] = {}
        self._all_item: QTreeWidgetItem | None = None
        self._change_counts: dict[str, int] = {}
        self._excluded: set[str] = set()
        self._updating = False  # Prevent recursive signals

    def set_projects(self, projects: dict[str, ProjectInfo]) -> None:
        """Populate the tree from project discovery results."""
        self.blockSignals(True)
        self._updating = True

        try:
            self._projects = projects
            self._change_counts = {}

            # Clean up exclusions to only include valid project names
            self._excluded = self._excluded & set(projects.keys())

            self.clear()

            # "All Projects" root item
            self._all_item = QTreeWidgetItem(self, ["All Projects"])
            self._all_item.setData(0, ROLE_PROJECT_NAME, "")
            self._all_item.setData(0, ROLE_ORIGINAL_LABEL, "All Projects")
            self._all_item.setExpanded(True)
            self._all_item.setFlags(
                self._all_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            self._all_item.setCheckState(0, Qt.CheckState.Checked)

            # Find top-level projects (not included by any other)
            included_by_others: set[str] = set()
            for p in projects.values():
                for inc in p.includes:
                    included_by_others.add(inc)

            top_level = sorted(
                [p for p in projects.values() if p.name not in included_by_others],
                key=lambda p: p.name,
            )

            for project in top_level:
                item = self._add_project_item(self._all_item, project, set())
                item.setExpanded(True)

            # Update "All Projects" check state based on children
            self._update_parent_check_state_no_recurse(self._all_item)
        finally:
            self._updating = False
            self.blockSignals(False)

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

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        project_name = item.data(0, ROLE_PROJECT_NAME)
        if project_name is not None:
            self.project_selected.emit(project_name)
        else:
            self.project_deselected.emit()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle checkbox state changes."""
        if self._updating:
            return

        project_name = item.data(0, ROLE_PROJECT_NAME)
        if project_name is None:
            return

        # Block signals to prevent recursive updates
        self.blockSignals(True)
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
            self.blockSignals(False)

        self.exclusions_changed.emit(self._excluded.copy())

    def _set_all_children_checked(
        self, parent: QTreeWidgetItem, checked: bool
    ) -> None:
        """Recursively set check state for all children."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(parent.childCount()):
            child = parent.child(i)
            child.setCheckState(0, state)
            self._set_all_children_checked(child, checked)

    def _update_children_exclusions(
        self, parent: QTreeWidgetItem, checked: bool
    ) -> None:
        """Recursively update exclusions for all children."""
        for i in range(parent.childCount()):
            child = parent.child(i)
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
            state = parent.child(i).checkState(0)
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
            self.blockSignals(True)
            self._updating = True
            try:
                self._restore_check_states(self._all_item)
            finally:
                self._updating = False
                self.blockSignals(False)

    def _restore_check_states(self, parent: QTreeWidgetItem) -> None:
        """Recursively restore check states from exclusion set."""
        for i in range(parent.childCount()):
            item = parent.child(i)
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
            state = parent.child(i).checkState(0)
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
        # Calculate change counts per project
        self._change_counts = {}
        if diff_summary:
            for project_name, tables in diff_summary.changes_by_project.items():
                count = sum(len(diffs) for diffs in tables.values())
                if count > 0:
                    self._change_counts[project_name] = count

        # Block signals to prevent itemChanged from firing during label updates
        self.blockSignals(True)
        self._updating = True

        try:
            # Update all items in the tree (returns accumulated count)
            if self._all_item:
                total = self._update_item_labels(self._all_item)
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
            self.blockSignals(False)

    def _update_item_labels(self, parent: QTreeWidgetItem) -> int:
        """Recursively update item labels with change counts.

        Returns the accumulated count for this subtree.
        """
        accumulated = 0

        for i in range(parent.childCount()):
            item = parent.child(i)
            project_name = item.data(0, ROLE_PROJECT_NAME)
            original_label = item.data(0, ROLE_ORIGINAL_LABEL)

            # Get this project's own count
            own_count = self._change_counts.get(project_name, 0) if project_name else 0

            # Recursively get children's counts
            child_count = self._update_item_labels(item)
            total_count = own_count + child_count
            accumulated += total_count

            if project_name and original_label:
                if total_count > 0:
                    item.setText(0, f"{original_label} ({total_count})")
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    item.setForeground(0, QColor(0, 100, 180))
                else:
                    item.setText(0, original_label)
                    font = item.font(0)
                    font.setBold(False)
                    item.setFont(0, font)
                    # Restore original color (gray for missing folders)
                    if project_name in self._projects:
                        has_folder = Path(
                            self._projects[project_name].local_path
                        ).is_dir()
                        if not has_folder:
                            item.setForeground(0, QColor(150, 150, 150))
                        else:
                            item.setForeground(0, QColor(0, 0, 0))

        return accumulated

    def clear_change_indicators(self) -> None:
        """Remove all change indicators."""
        self.update_change_indicators(None)
