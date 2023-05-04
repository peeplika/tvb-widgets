# -*- coding: utf-8 -*-
#
# "TheVirtualBrain - Widgets" package
#
# (c) 2022-2023, TVB Widgets Team
#

import os
import math
import mne
import numpy as np
import ipywidgets as widgets
import matplotlib.pyplot as plt
from abc import ABC, abstractmethod
from IPython.core.display_functions import display
from plotly_resampler import register_plotly_resampler, FigureWidgetResampler
from tvb.datatypes.time_series import TimeSeries
from tvbwidgets.core.ini_parser import parse_ini_file
from tvbwidgets.core.exceptions import InvalidInputException
from tvbwidgets.ui.base_widget import TVBWidget
from tvbwidgets.ui.widget_with_browser import TVBWidgetWithBrowser

mne.set_config('MNE_BROWSER_BACKEND', 'matplotlib')


class ABCDataWrapper(ABC):
    """ Wrap any TimeSeries for TSWidget to read/parse uniformly"""
    extra_dimensions = {1: ("State var.", None),
                        3: ("Mode", None)}
    CHANNEL_TYPE = "misc"
    MAX_DISPLAYED_TIMEPOINTS = 3000

    @property
    def data_shape(self):
        # type: () -> tuple
        return ()

    @property
    def displayed_time_points(self):
        # type: () -> int
        return min(self.data_shape[0], self.MAX_DISPLAYED_TIMEPOINTS)

    @abstractmethod
    def get_channels_info(self):
        # type: () -> (list, list, list)
        pass

    @abstractmethod
    def get_ts_period(self):
        # type: () -> float
        pass

    @abstractmethod
    def get_ts_sample_rate(self):
        # type: () -> float
        pass

    @abstractmethod
    def build_raw(self, np_slice=None):
        # type: (tuple) -> mne.io.RawArray
        pass

    @abstractmethod
    def get_update_slice(self, sel1, sel2):
        # type: (int, int) -> tuple
        pass

    @abstractmethod
    def get_slice_for_time_point(self, time_point, channel, sel1=0, sel2=0):
        # type: (int, int, int, int) -> tuple
        pass

    @abstractmethod
    def get_hover_channel_value(self, x, ch_index, sel1, sel2):
        # type: (float, int, int, int) -> float
        pass


class WrapperTVB(ABCDataWrapper):
    """ Wrap TVB TimeSeries object for tsWidget"""

    def __init__(self, data):
        # type: (TimeSeries) -> None
        if data is None or not isinstance(data, TimeSeries):
            raise InvalidInputException("Not a valid TVB TS " + str(data))
        self.data = data
        self.ch_names = []
        variables_labels = data.variables_labels
        if variables_labels is not None and variables_labels != []:
            sv_options = [(variables_labels[idx], idx) for idx in range(len(variables_labels))]
            self.extra_dimensions = ABCDataWrapper.extra_dimensions.copy()
            self.extra_dimensions[1] = ("State var.", sv_options)

    @property
    def data_shape(self):
        # type: () -> tuple
        return self.data.shape

    def get_channels_info(self):
        # type: () -> (list, list, list)
        no_channels = self.data.shape[2]  # number of channels is on axis 2

        if hasattr(self.data, "connectivity"):
            ch_names = self.data.connectivity.region_labels.tolist()
        elif hasattr(self.data, "sensors"):
            ch_names = self.data.sensors.labels.tolist()
        else:
            ch_names = ['signal-%d' % i for i in range(no_channels)]

        ch_order = list(range(no_channels))  # the order should be the order in which they are provided
        ch_types = [self.CHANNEL_TYPE for _ in ch_names]
        self.ch_names = ch_names
        return ch_names, ch_order, ch_types

    def get_ts_period(self):
        # type: () -> float
        displayed_period = self.data.sample_period * self.displayed_time_points
        return displayed_period

    def get_ts_sample_rate(self):
        # type: () -> float
        return self.data.sample_rate

    def build_raw(self, np_slice=None):
        # type: (tuple) -> mne.io.RawArray
        if np_slice is None:
            np_slice = self.get_update_slice()
        raw_info = mne.create_info(self.ch_names, sfreq=self.data.sample_rate)
        data_for_raw = self.data.data[np_slice].squeeze()
        data_for_raw = np.swapaxes(data_for_raw, 0, 1)
        raw = mne.io.RawArray(data_for_raw, raw_info, first_samp=self.data.start_time * self.data.sample_rate)
        return raw

    def get_update_slice(self, sel1=0, sel2=0):
        # type: (int, int) -> tuple
        new_slice = (slice(None), slice(sel1, sel1 + 1), slice(None), slice(sel2, sel2 + 1))
        return new_slice

    def get_slice_for_time_point(self, time_point, channel, sel1=0, sel2=0):
        # type: (int, int, int, int) -> tuple
        new_slice = (slice(time_point, time_point + 1), slice(sel1, sel1 + 1),
                     slice(channel, channel + 1), slice(sel2, sel2 + 1))
        return new_slice

    def get_hover_channel_value(self, x, ch_index, sel1, sel2):
        # type: (float, int, int, int) -> float
        time_per_tp = self.get_ts_period() / self.displayed_time_points  # time unit displayed in 1 time point

        # which time point to search in the time points array
        tp_on_hover = round((x - self.data.start_time) / time_per_tp)
        new_slice = self.get_slice_for_time_point(tp_on_hover, ch_index, sel1, sel2)

        ch_value = self.data.data[new_slice].squeeze().item(0)
        ch_value = round(ch_value, 4)

        return ch_value


class WrapperNumpy(ABCDataWrapper):
    """ Wrap a numpy array for tsWidget """

    def __init__(self, data, sample_rate, channel_names=None, ch_idx=2):
        # type: (np.ndarray, float, list, int) -> None
        """
        :param data: Numpy array 2D up to 4D
        :param sample_rate: float
        :param channel_names: optional names for channels
        :param ch_idx: Channels Index from the max 4D of the data. We assume time is on 0
        """
        if data is None or not isinstance(data, np.ndarray) or len(data.shape) <= 1:
            raise InvalidInputException("Not a valid numpy array %s \n "
                                        "It should be numpy.ndarray, at least 2D up to 4D" % str(data))
        self.data = data
        self.sample_rate = sample_rate
        self.ch_names = channel_names or []
        self.ch_idx = ch_idx

    @property
    def data_shape(self):
        # type: () -> tuple
        return self.data.shape

    def get_channels_info(self):
        # type: () -> (list, list, list)
        no_channels = self.data.shape[self.ch_idx]
        if (self.ch_names is None) or len(self.ch_names) != no_channels:
            self.ch_names = ['signal-%d' % i for i in range(no_channels)]
        ch_order = list(range(no_channels))  # the order should be the order in which they are provided
        ch_types = [self.CHANNEL_TYPE for _ in self.ch_names]
        return self.ch_names, ch_order, ch_types

    def get_ts_period(self):
        # type: () -> float
        sample_period = 1 / self.sample_rate
        displayed_period = sample_period * self.displayed_time_points
        return displayed_period

    def get_ts_sample_rate(self):
        # type: () -> float
        return self.sample_rate

    def build_raw(self, np_slice=None):
        # type: (tuple) -> mne.io.RawArray
        if np_slice is None:
            np_slice = self.get_update_slice()
        raw_info = mne.create_info(self.ch_names, sfreq=self.sample_rate)
        data_for_raw = self.data[np_slice]
        data_for_raw = data_for_raw.squeeze()
        data_for_raw = np.swapaxes(data_for_raw, 0, 1)
        raw = mne.io.RawArray(data_for_raw, raw_info)
        return raw

    def get_update_slice(self, sel1=0, sel2=0):
        # type: (int, int) -> tuple
        sel2 = sel2 if sel2 is not None else 0
        no_dim = len(self.data_shape)
        dim_to_slice_dict = {
            2: (slice(None), slice(None)),
            3: (slice(None), slice(sel1, sel1 + 1), slice(None)),
            4: (slice(None), slice(sel1, sel1 + 1), slice(None), slice(sel2, sel2 + 1))
        }
        new_slice = dim_to_slice_dict[no_dim]
        return new_slice

    def get_slice_for_time_point(self, time_point, channel, sel1=0, sel2=0):
        # type: (int, int, int, int) -> tuple
        sel1 = sel1 if sel1 is not None else 0
        sel2 = sel2 if sel2 is not None else 0
        no_dim = len(self.data_shape)
        dim_to_slice_dict = {
            2: (slice(time_point, time_point + 1), slice(channel, channel + 1)),
            3: (slice(time_point, time_point + 1), slice(sel1, sel1 + 1), slice(channel, channel + 1)),
            4: (slice(time_point, time_point + 1), slice(sel1, sel1 + 1),
                slice(channel, channel + 1), slice(sel2, sel2 + 1))
        }
        new_slice = dim_to_slice_dict[no_dim]
        return new_slice

    def get_hover_channel_value(self, x, ch_index, sel1, sel2):
        # type: (float, int, int, int) -> float
        time_per_tp = self.get_ts_period() / self.displayed_time_points  # time unit displayed in 1 time point

        # which time point to search in the time points array
        tp_on_hover = round(x / time_per_tp)
        new_slice = self.get_slice_for_time_point(tp_on_hover, ch_index, sel1, sel2)

        ch_value = self.data[new_slice].squeeze().item(0)
        ch_value = round(ch_value, 4)

        return ch_value


class TimeSeriesWidgetBase(widgets.VBox, TVBWidget):

    # =========================================== SETUP ================================================================
    def add_datatype(self, ts_tvb):
        # type: (TimeSeries) -> None
        data_wrapper = WrapperTVB(ts_tvb)
        self.logger.debug("Adding TVB TS for display...")
        self._populate_from_data_wrapper(data_wrapper)

    def add_data_array(self, numpy_array, sample_freq, ch_idx):
        # type: (np.array, float, int) -> None
        data_wrapper = WrapperNumpy(numpy_array, sample_freq, ch_idx=ch_idx)
        self._populate_from_data_wrapper(data_wrapper)

    def add_data(self, data, sample_freq=None, ch_idx=None):
        if isinstance(data, TimeSeries):
            self.add_datatype(data)
        else:
            self.add_data_array(data, sample_freq, ch_idx)

    def _populate_from_data_wrapper(self, data_wrapper):
        # type: (ABCDataWrapper) -> None
        if self.data is not None:
            raise InvalidInputException("TSWidget is not yet capable to display more than one TS, "
                                        "either use wid.reset_data, or create another widget instance!")

        self.data = data_wrapper
        self.sample_freq = data_wrapper.get_ts_sample_rate()
        self.displayed_period = data_wrapper.get_ts_period()
        self.ch_names, self.ch_order, self.ch_types = data_wrapper.get_channels_info()
        self.raw = self.data.build_raw()

    # ======================================== CHANNELS  ==============================================================
    def _unselect_all(self, _):
        self.logger.debug("Unselect all was called!")
        for cb_name in self.checkboxes:
            self.checkboxes[cb_name].value = False

    def _select_all(self, _):
        self.logger.debug("Select all was called!")
        for cb_name in self.checkboxes:
            self.checkboxes[cb_name].value = True

    def _create_checkboxes(self, array_wrapper, no_checkbox_columns=2):
        checkboxes_list, checkboxes_stack = [], []
        labels = array_wrapper.get_channels_info()[0]
        cb_per_col = math.ceil(len(labels) / no_checkbox_columns)  # number of checkboxes in a column
        for i, label in enumerate(labels):
            self.checkboxes[label] = widgets.Checkbox(value=True, description=label, indent=False,
                                                      layout=widgets.Layout(width='max-content'))
            if i and i % cb_per_col == 0:
                checkboxes_list.append(widgets.VBox(children=checkboxes_stack))
                checkboxes_stack = []
            checkboxes_stack.append(self.checkboxes[label])
        checkboxes_list.append(widgets.VBox(children=checkboxes_stack))
        checkboxes_region = widgets.HBox(children=checkboxes_list)
        return checkboxes_region

    def _create_select_unselect_all_buttons(self):
        select_all_btn = widgets.Button(description="Select all", layout=self.BUTTON_STYLE)
        select_all_btn.on_click(self._select_all)
        unselect_all_btn = widgets.Button(description="Unselect all", layout=self.BUTTON_STYLE)
        unselect_all_btn.on_click(self._unselect_all)
        return select_all_btn, unselect_all_btn

    def _create_dim_selection_buttons(self, array_wrapper):
        self.radio_buttons = []
        actions = []
        for idx, info in array_wrapper.extra_dimensions.items():
            extra_area, extra_radio_btn = self._create_selection(info[0], idx, dim_options=info[1])
            self.radio_buttons.append(extra_radio_btn)
            if extra_area is not None:
                actions.append(extra_area)

        return actions

    def _get_selection_values(self):
        sel1 = self.radio_buttons[0].value if self.radio_buttons[0] else None
        sel2 = self.radio_buttons[1].value if self.radio_buttons[1] else None
        return sel1, sel2

    def _create_selection(self, title="Mode", shape_pos=3, dim_options=None):
        if self.data is None or len(self.data.data_shape) <= max(2, shape_pos):
            return None, None

        no_dims = self.data.data_shape[shape_pos]
        if dim_options is None or dim_options == []:
            dim_options = [i for i in range(no_dims)]
        sel_radio_btn = widgets.RadioButtons(options=dim_options, layout={'width': 'max-content'})
        sel_radio_btn.observe(self._dimensions_selection_update, names=['value'])
        accordion = widgets.Accordion(children=[sel_radio_btn], selected_index=None, layout={'width': '20%'})
        accordion.set_title(0, title)
        return accordion, sel_radio_btn

    def _dimensions_selection_update(self, _):
        # update self.raw
        sel1, sel2 = self._get_selection_values()
        new_slice = self.data.get_update_slice(sel1, sel2)
        self.raw = self.data.build_raw(new_slice)


class TimeSeriesWidget(TimeSeriesWidgetBase):
    """ Actual TimeSeries Widget """

    def __init__(self, **kwargs):

        self.fig = None
        self.data = None
        self.ch_names = []
        self.ch_order = []
        self.ch_types = []
        self.displayed_period = 0
        self.no_channels = 30
        self.raw = None
        self.sample_freq = 0

        self.output = widgets.Output(layout=widgets.Layout(width='auto'))
        annotation_area = self._create_annotation_area()
        self.instr_area = self._create_instructions_region()
        self.title_area = widgets.HBox(children=[self.instr_area])

        self.checkboxes = dict()
        super().__init__([self.output, annotation_area, self.title_area], layout=self.DEFAULT_BORDER)
        self.logger.info("TimeSeries Widget initialized")

    # =========================================== SETUP ================================================================
    def reset_data(self):
        self.data = None
        self.title_area.children = [self.instr_area]

    def _populate_from_data_wrapper(self, data_wrapper):
        super()._populate_from_data_wrapper(data_wrapper=data_wrapper)
        self.channels_area = self._create_channel_selection_area(data_wrapper, 7)
        self.title_area.children += (self.channels_area,)
        self._redraw()

    # ======================================== CHANNEL VALUE AREA ======================================================
    def _create_annotation_area(self):
        title_label = widgets.Label(value='Channel values:')
        self.channel_val_area = widgets.VBox()
        annot_area = widgets.HBox(children=[title_label, self.channel_val_area], layout={'height': '70px',
                                                                                         'padding': '0 0 0 100px'})
        return annot_area

    # ===================================== INSTRUCTIONS DROPDOWN ======================================================
    @staticmethod
    def _create_instructions_region():
        instr_list, key_list, val_list = [], [], []
        help_text = parse_ini_file(os.path.join(os.path.dirname(__file__), "ts_widget_help.ini"))

        for key, value in help_text.items():
            key_label = widgets.Label(value=key)
            val_label = widgets.Label(value=value)
            key_list.append(key_label)
            val_list.append(val_label)

        instr_list.append(widgets.VBox(children=key_list))
        instr_list.append(widgets.VBox(children=val_list))
        instr_region = widgets.HBox(children=instr_list)
        instr_accordion = widgets.Accordion(children=[instr_region], selected_index=None,
                                            layout=widgets.Layout(width='40%'))
        instr_accordion.set_title(0, 'Keyboard shortcuts')
        return instr_accordion

    # =========================================== PLOT =================================================================
    def _redraw(self):
        def update_on_plot_interaction(_):
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
            self._update_fig()

        def hover(event):
            self.channel_val_area.children = []

            values = []  # list of label values for channels we are hovering over
            x = event.xdata  # time unit (s, ms) we are hovering over
            lines = self.fig.mne.traces  # all currently visible channels
            sel1, sel2 = self._get_selection_values()
            if event.inaxes == self.fig.mne.ax_main:
                for line in lines:
                    if line.contains(event)[0]:
                        line_index = lines.index(line)  # channel index among displayed channels
                        ch_index = self.fig.mne.picks[line_index]  # channel index among all channels
                        ch_name = self.fig.mne.ch_names[ch_index]

                        ch_value = self.data.get_hover_channel_value(x, ch_index, sel1, sel2)
                        label_val = f'{ch_name}: {ch_value}'
                        values.append(label_val)
                for v in values:
                    val_label = widgets.Label(value=v)
                    self.channel_val_area.children += (val_label,)

        # display the plot
        with plt.ioff():
            # create the plot
            self.fig = self.raw.plot(duration=self.displayed_period,
                                     n_channels=self.no_channels, show_first_samp=True,
                                     clipping=None, show=False)
            self.fig.set_size_inches(11, 5)
            # self.fig.figure.canvas.set_window_title('TimeSeries plot')
            self.fig.mne.ch_order = self.ch_order
            self.fig.mne.ch_types = np.array(self.ch_types)

            # add custom widget handling on keyboard and mouse events
            self.fig.canvas.mpl_connect('key_press_event', update_on_plot_interaction)
            self.fig.canvas.mpl_connect('button_press_event', update_on_plot_interaction)
            self.fig.canvas.mpl_connect("motion_notify_event", hover)
            with self.output:
                self.output.clear_output(wait=True)
                display(self.fig.canvas)

    # ======================================== CHANNELS  ==============================================================
    def _create_channel_selection_area(self, array_wrapper, no_checkbox_columns=7):
        # type: (ABCDataWrapper) -> widgets.Accordion
        """ Create the whole channel selection area: Select/Uselect all btns, State var. & Mode selection
            and Channel checkboxes
        """
        # checkboxes region
        checkboxes_region = self._create_checkboxes(array_wrapper=array_wrapper,
                                                    no_checkbox_columns=no_checkbox_columns)
        checkboxes_region.layout = widgets.Layout(width='590px', height='max-content')
        labels = array_wrapper.get_channels_info()[0]
        for label in labels:
            self.checkboxes[label].observe(self._update_ts, names="value", type="change")

        # select/unselec all buttons
        select_all_btn, unselect_all_btn = self._create_select_unselect_all_buttons()
        actions = [select_all_btn, unselect_all_btn]

        # select dimensions buttons (state var. & mode)
        actions.extend(self._create_dim_selection_buttons(array_wrapper=array_wrapper))

        # add all buttons to channel selection area
        channels_region = widgets.VBox(children=[widgets.HBox(actions), checkboxes_region])
        channels_area = widgets.Accordion(children=[channels_region], selected_index=None,
                                          layout=widgets.Layout(width='50%'))
        channels_area.set_title(0, 'Channels')
        return channels_area

    def _dimensions_selection_update(self, _):
        # update self.raw and linked parts
        super()._dimensions_selection_update(_)

        # update plot
        self.fig = self.raw.plot(duration=self.displayed_period,
                                 n_channels=self.no_channels,
                                 clipping=None, show=False)
        self.fig.set_size_inches(11, 5)

        self._redraw()

        # refresh the checkboxes if they were unselected
        for cb_name in self.checkboxes:
            self.checkboxes[cb_name].value = True

    def _update_ts(self, val):
        ch_names = list(self.fig.mne.ch_names)
        self.logger.debug("Update_ts is called for channels " + str(ch_names))

        # check if newly checked option is before current ch_start in the channels list
        if (val['old'] is False) and (val['new'] is True):
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

        # for unselect all
        if not picks:
            self.fig.mne.picks = picks
            self.fig.mne.n_channels = 0
            self._update_fig()
            return

        # if not enough values are checked, force the plot to display less channels
        n_channels = self.fig.mne.n_channels
        if (len(picks) < n_channels) or (n_channels < len(picks) <= self.no_channels):
            self.fig.mne.n_channels = len(picks)
        else:
            self.fig.mne.n_channels = self.no_channels

        # order list of checked channels according to ordering rule (self.fig.mne.ch_order)
        ch_order_filtered = [x for x in self.fig.mne.ch_order if x not in not_picked]

        ch_start = self.fig.mne.ch_start
        ch_start_number = self.fig.mne.ch_order[ch_start]
        if ch_start_number not in ch_order_filtered:  # if first channel was unchecked
            ch_start = self._get_next_checked_channel(ch_start, ch_order_filtered, list(self.fig.mne.ch_order))

        self.fig.mne.ch_start = ch_start
        ch_start_number = self.fig.mne.ch_order[self.fig.mne.ch_start]
        ch_start_index = ch_order_filtered.index(ch_start_number)

        new_picks = np.array(ch_order_filtered[ch_start_index:(ch_start_index + self.fig.mne.n_channels)])
        self.fig.mne.n_channels = len(new_picks)  # needed for WID-66
        self.fig.mne.picks = new_picks
        ch_start_index = list(self.fig.mne.ch_order).index(new_picks[0])
        self.fig.mne.ch_start = ch_start_index

        self._update_fig()

    @staticmethod
    def _get_next_checked_channel(ch_start, checked_list, ch_order):
        for _ in range(len(ch_order)):
            ch_start += 1
            new_ch_start_number = ch_order[ch_start]  # get the number representation of next first channel
            if new_ch_start_number in checked_list:
                break
        return ch_start

    def _update_fig(self):
        self.fig._update_trace_offsets()
        self.fig._update_vscroll()
        try:
            self.fig._redraw(annotations=True)
        except:
            self.fig._redraw(update_data=False)  # needed in case of Unselect all


class TimeSeriesWidgetPlotly(TimeSeriesWidgetBase):
    """ TimeSeries Widget drawn using plotly"""

    def __init__(self, **kwargs):
        # data
        self.fig = None
        self.data = None
        self.ch_names = []
        self.raw = None
        self.sample_freq = 0
        self.start_time = 0
        self.end_time = 0
        self.std_step = 0

        # plot & UI
        self.checkboxes = dict()
        self.plot_and_channels_area = widgets.HBox()
        self.output = widgets.Output(layout=widgets.Layout(width='75%'))
        self.channel_selection_area = widgets.HBox(layout=widgets.Layout(width='25%', height='700px',
                                                                         margin="50px 0px 0px 0px"))
        self.plot_and_channels_area.children += (self.output, self.channel_selection_area)
        self.timeline_title = widgets.Label(value='Adjust timeframe')
        self.timeline_scrollbar = widgets.IntRangeSlider(value=[0, 0], layout=widgets.Layout(width='30%'))

        super().__init__([self.plot_and_channels_area, self.timeline_title, self.timeline_scrollbar],
                         layout=self.DEFAULT_BORDER)
        self.logger.info("TimeSeries Widget with Plotly initialized")

    # =========================================== SETUP ================================================================
    def _populate_from_data_wrapper(self, data_wrapper):
        super()._populate_from_data_wrapper(data_wrapper=data_wrapper)
        del self.ch_order, self.ch_types  # delete these as we don't use them in plotly
        self.channels_area = self._create_channel_selection_area(array_wrapper=data_wrapper)
        self._setup_timeline_scrollbar()
        self.channel_selection_area.children += (self.channels_area,)
        self.plot_ts_with_plotly()

    # =========================================== PLOT =================================================================
    def add_traces(self, data=None, ch_names=None):
        # create traces for each signal
        data_from_raw, times = self.raw[:, :]
        data = data if data is not None else data_from_raw
        ch_names = ch_names if ch_names is not None else self.ch_names

        # traces will be added from bottom to top, so reverse the lists to put the first channel on top
        data = data[::-1]
        ch_names = ch_names[::-1]

        self.std_step = 5 * np.max(np.std(data, axis=1))
        self.fig.add_traces(
            [dict(y=ts + i * self.std_step, name=ch_name, customdata=ts, hovertemplate='%{customdata}')
             for i, (ch_name, ts) in enumerate(zip(ch_names, data))]
        )

        # display channel names for each trace
        for i, ch_name in enumerate(ch_names):
            self.fig.add_annotation(
                x=0.0, y=i * self.std_step,
                text=ch_name,
                showarrow=False,
                xref='paper',
                xshift=-70
            )

        # add ticks between channel names and their traces
        self.fig.update_yaxes(fixedrange=False, showticklabels=False, ticks='outside', ticklen=3,
                              tickvals=np.arange(len(ch_names)) * self.std_step)

        # configure legend
        self.fig.update_layout(
            # traces are added from bottom to top, but legend displays the names from top to bottom
            legend={'traceorder': 'reversed'}
        )

        # sync plot timeline with selected slider range
        self.fig.update_layout(xaxis_range=list(self.timeline_scrollbar.value))

    def add_visibility_buttons(self):
        # buttons to show/hide all traces
        self.fig.update_layout(dict(updatemenus=[dict(type="buttons", direction="left",
                                                      buttons=list([dict(args=["visible", True], label="Show All",
                                                                         method="restyle"),
                                                                    dict(args=["visible", False], label="Hide All",
                                                                         method="restyle")
                                                                    ]),
                                                      showactive=False,  # personal preference
                                                      # position buttons in top right corner of plot
                                                      x=1,
                                                      xanchor="right",
                                                      y=1.1,
                                                      yanchor="top")]
                                    ))

    def create_plot(self, data=None, ch_names=None):
        # register resampler so every plot will benefit from it
        register_plotly_resampler(mode='auto')

        self.fig = FigureWidgetResampler()

        self.add_traces(data, ch_names)

        # different visual settings
        self.fig.update_layout(
            width=1000, height=800,
            showlegend=True,
            template='plotly_white'
        )

        self.add_visibility_buttons()

    def plot_ts_with_plotly(self, data=None, ch_names=None):
        self.create_plot(data, ch_names)
        with self.output:
            self.output.clear_output(wait=True)
            display(self.fig)

    # ================================================ TIMELINE ========================================================
    def _setup_timeline_scrollbar(self):
        # get start and end times
        _, times = self.raw[:, :]
        self.start_time = self.raw.time_as_index(times)[0]
        self.end_time = self.raw.time_as_index(times)[-1]

        # set values from slider
        self.timeline_scrollbar.min = self.start_time
        self.timeline_scrollbar.max = self.end_time

        # compute step for slider according to TS length (always try to have 10 steps)
        ts_length = len(times)
        step = ts_length // 10
        step = int(math.ceil(step / 10)) * 10  # round up to nearest 10
        self.timeline_scrollbar.step = step
        self.timeline_scrollbar.value = [self.start_time, self.end_time]
        # self.timeline_scrollbar.continuous_update = False # uncomment this to update plot only on slider release
        self.timeline_scrollbar.observe(self.update_timeline, names='value', type='change')

    def update_timeline(self, val):
        """ Set the plot timeline to the values from slider"""
        new_range = val['new']
        self.start_time, self.end_time = new_range
        self.fig.update_layout(xaxis_range=list(new_range))

    # =========================================== CHANNELS SELECTION ===================================================

    def _create_channel_selection_area(self, array_wrapper, no_checkbox_columns=2):
        # type: (ABCDataWrapper) -> widgets.Accordion
        """ Create the whole channel selection area: Submit button to update plot, Select/Uselect all btns,
            State var. & Mode selection and Channel checkboxes
        """
        # checkboxes
        checkboxes_region = self._create_checkboxes(array_wrapper=array_wrapper,
                                                    no_checkbox_columns=no_checkbox_columns)
        for cb_stack in checkboxes_region.children:
            cb_stack.layout = widgets.Layout(width='50%')

        # selection submit button
        self.submit_selection_btn = widgets.Button(description='Submit selection', layout=self.BUTTON_STYLE)
        self.submit_selection_btn.on_click(self._update_ts)

        # select/unselect all buttons
        select_all_btn, unselect_all_btn = self._create_select_unselect_all_buttons()

        # select dimensions buttons (state var. & mode)
        selections = self._create_dim_selection_buttons(array_wrapper=array_wrapper)
        for selection in selections:
            selection.layout = widgets.Layout(width='50%')

        # add all buttons to channel selection area
        channels_region = widgets.VBox(children=[self.submit_selection_btn, widgets.HBox(selections),
                                                 widgets.HBox([select_all_btn, unselect_all_btn]),
                                                 checkboxes_region])
        channels_area = widgets.Accordion(children=[channels_region], selected_index=None,
                                          layout=widgets.Layout(width='70%'))
        channels_area.set_title(0, 'Channels')
        return channels_area

    def _update_ts(self, btn):
        self.logger.debug('Updating TS')
        ch_names = list(self.ch_names)

        # save selected channels using their index in the ch_names list
        picks = []
        for cb in list(self.checkboxes.values()):
            ch_index = ch_names.index(cb.description)  # get the channel index
            if cb.value:
                picks.append(ch_index)  # list with number representation of channels

        # if unselect all
        # TODO: should we remove just the traces and leave the channel names and the ticks??
        if not picks:
            self.fig.data = []  # remove traces
            self.fig.layout.annotations = []  # remove channel names
            self.fig.layout.yaxis.tickvals = []  # remove ticks between channel names and traces
            return

        # get data and names for selected channels; self.raw is updated before redrawing starts
        data, _ = self.raw[:, :]
        data = data[picks, :]
        ch_names = [ch_names[i] for i in picks]

        # redraw the entire plot
        self.plot_ts_with_plotly(data, ch_names)


class TimeSeriesBrowser(widgets.VBox, TVBWidgetWithBrowser):

    def __init__(self, collab=None, folder=None):
        super().__init__(**{'collab': collab, 'folder': folder})
        timeseries_button = widgets.Button(description='View time series')
        self.buttons = widgets.HBox([timeseries_button], layout=widgets.Layout(margin="0px 0px 0px 20px"))
        self.timeseries_widget = TimeSeriesWidget()
        self.children = [self.storage_widget, self.buttons, self.message_label, self.timeseries_widget]

        def add_timeseries_datatype(_):
            self.load_selected_file(TimeSeries, ('.h5', '.npz'))

        timeseries_button.on_click(add_timeseries_datatype)

    def add_datatype(self, datatype):  # type: (TimeSeries) -> None
        self.timeseries_widget.reset_data()
        self.timeseries_widget.add_datatype(datatype)
