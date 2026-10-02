from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from ui.SearchableComboBox import SearchableComboBox


app = QApplication.instance() or QApplication([])


def test_search_matches_multiple_terms_and_hidden_metadata():
    combo = SearchableComboBox()
    combo.addItem("#12 · Fix login [OPEN] (Alice)", {"id": 12})
    combo.setItemData(0, "feature/auth", Qt.UserRole + 1)
    combo.addItem("#34 · Payment retry [OPEN] (Bob)", {"id": 34})
    combo.setItemData(1, "feature/billing", Qt.UserRole + 1)

    combo._filter_items("bob billing")
    assert combo.search_list.count() == 1
    assert combo.search_list.item(0).data(Qt.UserRole) == 1
    combo._choose_item(combo.search_list.item(0))
    assert combo.currentData()["id"] == 34

    combo._filter_items("12 alice")
    assert combo.search_list.count() == 1
    assert combo.search_list.item(0).data(Qt.UserRole) == 0
