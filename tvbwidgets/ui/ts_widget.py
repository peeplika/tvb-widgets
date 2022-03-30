# -*- coding: utf-8 -*-
#
# "TheVirtualBrain - Widgets" package
#
# (c) 2022-2023, TVB Widgets Team
#
import ipywidgets as widgets
import math
import matplotlib.pyplot as plt
import mne
import numpy as np
import os
from abc import ABC, abstractmethod
from IPython.core.display_functions import display
from tvb.datatypes.time_series import TimeSeries
from tvbwidgets.core.ini_parser import parse_ini_file
from tvbwidgets.ui.base_widget import TVBWidget


class TSWidgetBuilder(ABC):
    """ Blueprint for builder of TS Widget """

    @abstractmethod
    def configure_ch_names(self):
        pass

    @abstractmethod
    def configure_displayed_period(self):
        pass

    @abstractmethod
    def configure_ch_order(self):
        pass

    @abstractmethod
    def configure_ch_types(self):
        pass

    @abstractmethod
    def create_raw(self):
        pass

    @abstractmethod
    def create_ts_widget(self):
        pass


class TSWidgetBuilderFromTVB(TSWidgetBuilder):
    """ Builder of TS Widget using TVB TimeSeries as input """

    def __init__(self, data, *kwargs):
        self.data = data
        self.ts_widget = TimeSeriesWidget(self.data)

    # ====================================== CONFIGURATION =============================================================
    def configure_ch_names(self):
        no_channels = self.data.shape[2]  # number of channels is on axis 2
        self.ts_widget.ch_names = [str(ch) for ch in list(range(no_channels))]  # list should contain str

    def configure_displayed_period(self):
        total_period = self.data.summary_info()['Length']
        self.ts_widget.displayed_period = total_period / 10  # chose to display a tenth of the total duration

    def configure_ch_order(self):
        no_channels = self.data.shape[2]  # number of channels is on axis 2
        self.ts_widget.ch_order = list(range(no_channels))  # the order should be the order in which they are provided

    def configure_ch_types(self):
        types = ['misc' for _ in self.ts_widget.ch_names]
        self.ts_widget.ch_types = types

    # ======================================= RAW OBJECT ===============================================================
    # TODO: maybe this should remain inside TSWidget class, as it is used (with small modifications) when changing the
    #       state variable/mode as well?
    def create_raw(self):
        # create Info object for Raw object
        raw_info = mne.create_info(self.ts_widget.ch_names, sfreq=self.data.sample_rate)

        data_for_raw = self.data.data[:, 0, :, 0]  # plot is drawn for first time

        data_for_raw = np.swapaxes(data_for_raw, 0, 1)
        raw = mne.io.RawArray(data_for_raw, raw_info)
        self.ts_widget.raw = raw

    # ======================================== TS WIDGET ===============================================================

    def create_ts_widget(self):
        self.configure_ch_names()
        self.configure_displayed_period()
        self.configure_ch_order()
        self.configure_ch_types()
        self.create_raw()

        self.ts_widget.create_checkboxes()
        return self.ts_widget


# TODO: finish builder from np array
class TSWidgetBuilderFromNumpy():
    """ Builder of TS Widget using numpy arrays as input """

    def __init__(self, data, sample_freq, *kwargs):
        self.data = data
        self.sample_freq = sample_freq
        self.ts_widget = TimeSeriesWidget(self.data)


class TSWidgetFactory:
    """ Orchestrates the creation of the TS Widget using the correct builder """

    def __init__(self, data):
        self.data = data
        self.sample_freq = None
        self.builder = None

        self.set_builder()

    def set_builder(self):
        if isinstance(self.data, TimeSeries):
            self.builder = TSWidgetBuilderFromTVB(self.data)
        elif isinstance(self.data, np.ndarray):
            self.builder = TSWidgetBuilderFromNumpy(self.data, self.sample_freq)

    def create_ts_widget(self):
        return self.builder.create_ts_widget()


class TimeSeriesWidget(widgets.VBox, TVBWidget):
    """ Actual TS Widget """

    def __init__(self, data, **kwargs):
        super().__init__(**kwargs)
        self.fig = None

        # data
        self.data = data
        self.data_type = None
        self.ch_names = []
        self.ch_order = []
        self.ch_types = []
        self.displayed_period = 0
        self.no_channels = 30
        self.selected_state_var = 0
        self.selected_mode = 0
        self.raw = None

        # UI elements
        self.output = widgets.Output(layout=widgets.Layout(width='auto'))

        # buttons region
        self.select_all_btn = widgets.Button(description="Select all")
        self.select_all_btn.on_click(self.select_all)
        self.unselect_all_btn = widgets.Button(description="Unselect all")
        self.unselect_all_btn.on_click(self.unselect_all)

        # instructions region
        self.instr_list = []
        self.create_instructions()
        self.instr_region = widgets.HBox(children=self.instr_list)
        self.instr_accordion = widgets.Accordion(children=[self.instr_region], selected_index=None)
        self.instr_accordion.set_title(0, 'Keyboard shortcuts')

        # select dimensions region
        self.create_state_var_selection()
        self.state_var_accordion = widgets.Accordion(children=[self.state_var_radio_btn], selected_index=None)
        self.state_var_accordion.set_title(0, 'State variable')
        self.create_mode_selection()
        self.mode_accordion = widgets.Accordion(children=[self.mode_radio_btn], selected_index=None)
        self.mode_accordion.set_title(0, 'Mode')

        self.selection_buttons = widgets.HBox(children=[self.select_all_btn, self.unselect_all_btn])
        self.dropdown_region = widgets.HBox(children=[self.state_var_accordion, self.mode_accordion,
                                                      self.instr_accordion])

        # checkboxes region
        self.checkboxes = dict()
        self.checkboxes_list = []

    # ========================================== BUTTONS ===============================================================
    # buttons methods
    def unselect_all(self, btn):
        for cb_name in self.checkboxes:
            self.checkboxes[cb_name].value = False

    def select_all(self, btn):
        for cb_name in self.checkboxes:
            self.checkboxes[cb_name].value = True

    # ===================================== INSTRUCTIONS DROPDOWN ======================================================
    def create_instructions(self):
        help_text = parse_ini_file(os.path.join(os.path.dirname(__file__), "ts_widget_help.ini"))
        key_list = []
        val_list = []
        for key, value in help_text.items():
            key_label = widgets.Label(value=key)
            val_label = widgets.Label(value=value)

            key_list.append(key_label)
            val_list.append(val_label)

        self.instr_list.append(widgets.VBox(children=key_list))
        self.instr_list.append(widgets.VBox(children=val_list))

    # ====================================== DIMENSIONS SELECTION ======================================================
    def create_state_var_selection(self):
        no_state_vars = self.data.shape[1]
        state_vars = [i for i in range(no_state_vars)]
        self.state_var_radio_btn = widgets.RadioButtons(options=state_vars, layout={'width': 'max-content'})
        self.state_var_radio_btn.observe(self.state_var_update, names=['value'])

    def create_mode_selection(self):
        no_modes = self.data.shape[3]
        modes = [i for i in range(no_modes)]
        self.mode_radio_btn = widgets.RadioButtons(options=modes, layout={'width': 'max-content'})
        self.mode_radio_btn.observe(self.mode_update, names=['value'])

    def mode_update(self, s):
        selected = self.mode_radio_btn.value
        self.selected_mode = selected
        self.dimensions_selection_update()

    def state_var_update(self, s):
        selected = self.state_var_radio_btn.value
        self.selected_state_var = selected
        self.dimensions_selection_update()

    def update_data_for_plot(self):
        # create Info object for Raw object
        raw_info = mne.create_info(self.ch_names, sfreq=self.data.sample_rate)

        state_var = self.selected_state_var
        mode = self.selected_mode
        data_for_raw = self.data.data[:, state_var, :, mode]

        data_for_raw = np.swapaxes(data_for_raw, 0, 1)
        raw = mne.io.RawArray(data_for_raw, raw_info)
        self.raw = raw

    def dimensions_selection_update(self):
        # there should not be any need to update anything else besides self.raw
        self.update_data_for_plot()
        self.fig = self.raw.plot(duration=self.displayed_period, n_channels=self.no_channels, clipping=None,
                                 show=False)
        self.fig.set_size_inches(11, 7)

        with self.output:
            self.output.clear_output(wait=True)
            display(self.fig.canvas)

        # refresh the checkboxes if they were unselected
        for cb_name in self.checkboxes:
            self.checkboxes[cb_name].value = True

    # =========================================== PLOT =================================================================
    def get_widget(self):
        def update_on_plot_interaction(event):
            """
            Function that updates the checkboxes when the user navigates through the plot
            using either the mouse or the keyboard
            """
            picks = list(self.fig.mne.picks)
            for ch in picks:
                ch_name = self.fig.mne.ch_names[ch]
                cb = self.checkboxes[ch_name]
                if not cb.value:
                    cb.value = True

        # display the plot
        with plt.ioff():
            # create the plot
            self.fig = self.raw.plot(duration=self.displayed_period, n_channels=self.no_channels, clipping=None,
                                     show=False)
            self.fig.set_size_inches(11, 7)
            # self.fig.figure.canvas.set_window_title('TimeSeries plot')
            self.fig.mne.ch_order = self.ch_order
            self.fig.mne.ch_types = np.array(self.ch_types)

            # add custom widget handling on keyboard and mouse events
            self.fig.canvas.mpl_connect('key_press_event', update_on_plot_interaction)
            self.fig.canvas.mpl_connect('button_press_event', update_on_plot_interaction)

            with self.output:
                display(self.fig.canvas)

        items = [self.channels_accordion, self.dropdown_region, self.output]
        grid = widgets.GridBox(items)
        return grid

    # ======================================== CHECKBOXES ==============================================================
    def create_checkboxes(self):
        """ This will be called from builder, because it needs the initializations done by it"""
        checkboxes_stack = []
        labels = self.ch_names
        cb_per_col = math.ceil(len(labels) / 8)  # number of checkboxes in a column; should always display 8 cols
        for i, label in enumerate(labels):
            self.checkboxes[label] = widgets.Checkbox(value=True,
                                                      description=str(label),
                                                      disabled=False,
                                                      indent=False)
            self.checkboxes[label].observe(self.update_ts, names="value", type="change")
            if i and i % cb_per_col == 0:
                self.checkboxes_list.append(widgets.VBox(children=checkboxes_stack))
                checkboxes_stack = []
            checkboxes_stack.append(self.checkboxes[label])
        self.checkboxes_list.append(widgets.VBox(children=checkboxes_stack))

        self.checkboxes_region = widgets.HBox(children=self.checkboxes_list,
                                              layout=widgets.Layout(height='250px'))
        self.channels_region = widgets.VBox(children=[self.checkboxes_region, self.selection_buttons])
        self.channels_accordion = widgets.Accordion(children=[self.channels_region], selected_index=None,
                                                    layout=widgets.Layout(width='40%'))
        self.channels_accordion.set_title(0, 'Channels')

    def update_ts(self, val):
        ch_names = list(self.fig.mne.ch_names)
        os.write(1, f"{val['owner']}\n".encode())

        # check if newly checked option is before current ch_start in the channels list
        if val['old'] == False and val['new'] == True:
            ch_name = val['owner'].description
            ch_number = ch_names.index(ch_name)
            ch_changed_index = list(self.fig.mne.ch_order).index(ch_number)
            if ch_changed_index < self.fig.mne.ch_start:
                self.fig.mne.ch_start = ch_changed_index

        # divide list of all channels into checked(picked) and unchecked(not_picked) channels
        picks = []
        not_picked = []
        for cb in list(self.checkboxes.values()):
            ch_number = ch_names.index(cb.description)  # get the number representation of checked/unchecked channel
            if cb.value:
                picks.append(ch_number)  # list with number representation of channels
            else:
                not_picked.append(ch_number)

        if not picks:
            self.fig.mne.picks = picks
            self.fig.mne.n_channels = 0
            self.update_fig()
            return

        # if not enough values are checked, force the plot to display less channels
        n_channels = self.fig.mne.n_channels
        if (len(picks) < n_channels) or (n_channels < len(picks) <= self.no_channels):
            self.fig.mne.n_channels = len(picks)
        else:
            self.fig.mne.n_channels = self.no_channels

        # order list of checked channels according to self.fig.mne.ch_order
        ch_order_filtered = [x for x in self.fig.mne.ch_order if x not in not_picked]

        ch_start = self.fig.mne.ch_start
        ch_start_number = self.fig.mne.ch_order[ch_start]
        if ch_start_number not in ch_order_filtered:  # if first channel was unchecked
            ch_start = self.get_next_checked_channel(ch_start, ch_order_filtered, list(self.fig.mne.ch_order))

        self.fig.mne.ch_start = ch_start
        ch_start_number = self.fig.mne.ch_order[self.fig.mne.ch_start]
        ch_start_index = ch_order_filtered.index(ch_start_number)

        new_picks = np.array(ch_order_filtered[ch_start_index:(ch_start_index + self.fig.mne.n_channels)])
        self.fig.mne.picks = new_picks
        # self.fig.mne.n_channels = len(self.fig.mne.picks)
        ch_start_index = list(self.fig.mne.ch_order).index(new_picks[0])
        self.fig.mne.ch_start = ch_start_index

        self.update_fig()

    @staticmethod
    def get_next_checked_channel(ch_start, checked_list, ch_order):
        for _ in range(len(ch_order)):
            ch_start += 1
            new_ch_start_number = ch_order[ch_start]  # get the number representation of next first channel
            if new_ch_start_number in checked_list:
                break
        return ch_start

    def update_fig(self):
        self.fig._update_trace_offsets()
        self.fig._update_vscroll()
        try:
            self.fig._redraw(annotations=True)
        except:
            self.fig._redraw(update_data=False)  # needed in case of Unselect all
