from textual.widget import Widget
from textual.widgets import Label
class InboxView(Widget):
    def compose(self):
        yield Label("Inbox — coming in Task 11")
