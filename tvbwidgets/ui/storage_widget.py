# -*- coding: utf-8 -*-
#
# "TheVirtualBrain - Widgets" package
#
# (c) 2022-2023, TVB Widgets Team
#
import ipywidgets

from tvbwidgets.ui.base_widget import TVBWidget
from tvbwidgets.ui.drive_widget import DriveWidget


class StorageWidget(ipywidgets.Tab, TVBWidget):

    def __init__(self, **kwargs):
        tab1 = ipywidgets.VBox()
        tab2 = DriveWidget()
        tab3 = ipywidgets.VBox()

        super().__init__([tab1, tab2, tab3], selected_index=1, **kwargs)

        self.set_title(0, 'Current Selection')
        self.set_title(1, 'Drive')
        self.set_title(2, 'Bucket')
        # TODO uniform API for all tabs
        self.api = tab2

    def get_file_content(self, file_name):
        return self.api.get_file_content(file_name)