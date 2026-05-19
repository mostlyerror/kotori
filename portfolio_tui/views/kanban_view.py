from textual.widget import Widget
from textual.widgets import Label
class KanbanView(Widget):
    def compose(self):
        yield Label("Kanban — coming in Task 13")
