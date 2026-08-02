import numpy as np

from pathlib import Path

from pyqtgraph.parametertree import Parameter

from pymodaq.utils.shared_ui import SharedUI
from pymodaq_data import DataToExport, DataWithAxes, DataCalculated, Axis
from pymodaq_data.h5modules.data_saving import DataLoader
from pymodaq_gui.h5modules.saving import H5Saver
from pymodaq_gui.managers.parameter_manager import ParameterManager
from pymodaq_gui.plotting.data_viewers import ViewerDispatcher
from pymodaq_gui.qt_utils import mkQApp
from pymodaq_gui.utils import DockArea, Dock, CustomApp
from pymodaq_gui.utils.widgets.window import make_window
from pymodaq_utils.math_utils import find_index


class HistogramPlot(CustomApp):

    params = [
        {'title': 'Histo:', 'name': 'histo', 'type': 'group', 'children': [
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 300.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 900.},
            {'title': 'AutoBin:', 'name': 'autobin', 'type': 'led', 'value': True},
            {'title': 'Nbin:', 'name': 'nbins', 'type': 'int', 'value': 100, 'readonly': True},
            ]},

    ]
    def __init__(self, h5saver: H5Saver | Path | str,
                 dockarea: DockArea | None = None,
                 title='Histogram'):
        super().__init__(dockarea, title=title)
        self.h5saver = h5saver
        self._xaxis_name: str = None

        self.viewer = ViewerDispatcher(dockarea=dockarea)

    def value_changed(self, param: Parameter):
        if param.name() == 'autobin':
            self.settings.child('histo', 'nbins').setOpts(readonly=param.value())
            self.compute_plot_histogram(self._xaxis_name)
        elif param.name() == 'nbins' and not self.settings['histo', 'autobin']:
            if self._xaxis_name is not None:
                self.compute_plot_histogram(self._xaxis_name)

    def compute_plot_histogram(self, xaxis_name: str):
        dte = self.compute_histogram(xaxis_name)
        self.viewer.show_data(dte)

    def compute_histogram(self, xaxis_name: str) -> DataToExport:
        self._xaxis_name = xaxis_name
        with DataLoader(self.h5saver, swmr_mode=True) as dl:
            dte = dl.load_all(where='/RawData')

        xdwa = dte.pop(dte.index_from_name_origin(xaxis_name))
        ((istart, vstart), (istop, vstop)) = find_index(
            xdwa[0], threshold=[self.settings['histo', 'start'],
                                self.settings['histo', 'stop']])

        nav_index = xdwa.nav_indexes[0]

        xdwa_sliced = xdwa.inav[istart:istop]

        # first compute bins from the timestamps
        timestamps = xdwa_sliced.get_axis_from_index(nav_index)[0].get_data()
        bin_edges = np.histogram_bin_edges(
            timestamps,
            bins='auto' if self.settings['histo', 'autobin'] else self.settings['histo', 'nbins'])

        # then compute the bin index for each timestamp
        indexes = np.digitize(timestamps, bin_edges)

        # average actuator data in their corresponding bins:
        averaged_actuator_values = np.atleast_1d(self.average_data_over_indexes(xdwa_sliced[0], indexes))

        dte_out = DataToExport('Histogram')

        for dwa in dte:
            indexes = np.digitize(dwa.get_axis_from_index(nav_index)[0].get_data(), bin_edges)
            dte_out.append(DataCalculated(
                dwa.name, origin=dwa.origin,
                data = [np.atleast_1d(self.average_data_over_indexes(dwa[ind], indexes)) for ind in
                        range(len(dwa))],
                axes = [Axis(label = xdwa.name, units=xdwa.units,
                             data=averaged_actuator_values)] +
                       [dwa.get_axis_from_index(ind)[0] for ind in dwa.sig_indexes],
                labels=dwa.labels,
                units = dwa.units,
            ))
        return dte_out

    @staticmethod
    def average_data_over_indexes(data: np.ndarray[float],
                                  indexes: np.ndarray[int]) -> np.ndarray[float]:
        """ Return the average  of data over the indexes

        See: https://stackoverflow.com/questions/71329884/python-numpy-get-average-of-array-based-on-index
        """
        one_hot = np.eye(np.max(indexes) + 1)[indexes]

        counts = np.sum(one_hot, axis=0)
        return np.sum((one_hot.T * data), axis=1) / counts


if __name__ == '__main__':
    app = mkQApp('Histogram')

    file_path = r'C:\Data\2026\20260802\Dataset_20260802_024.h5'

    win, area = make_window(title='Histogram', flags=None)
    histo = HistogramPlot(file_path, dockarea=area)
    dock_settings = Dock('Settings')
    dock_settings.addWidget(histo.settings_tree)
    area.addDock(dock_settings)
    shared_ui = SharedUI(win)
    shared_ui.affect_application(histo)
    histo.compute_plot_histogram('TempControl')

    app.exec()