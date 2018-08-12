import numpy as np
import pandas as pd
import xarray as xr
import attr
from attr.validators import instance_of
import pathlib

from analog_trace import AnalogTraceAnalyzer
from fluo_metadata import FluoMetadata


@attr.s(slots=True)
class SingleFovParser:
    """ Analyze a single FOV with fluorescent and analog data """

    analog_fname = attr.ib(validator=instance_of(pathlib.Path))
    fluo_fname = attr.ib(validator=instance_of(pathlib.Path))
    metadata = attr.ib(validator=instance_of(FluoMetadata))
    analog_analyzed = attr.ib(init=False, repr=False)  # AnalogTraceAnalyzer instance
    all_fluo_results = attr.ib(init=False, repr=False)  # all data generated by CaImAn
    fluo_trace = attr.ib(init=False, repr=False)  # The specific dF/F trace of that file
    fluo_analyzed = attr.ib(init=False)  # DataArray with the different slices of run, stim and dF/F

    def parse(self):
        """ Main method to parse a single duo of analog and fluorescent data """
        self.all_fluo_results = np.load(str(self.fluo_fname))
        self.fluo_trace = self.all_fluo_results['F_dff']
        analog_data = pd.read_table(self.analog_fname, header=None,
                                    names=['stimulus', 'run'], index_col=False)
        self.analog_analyzed = AnalogTraceAnalyzer(tif_filename=str(self.analog_fname), 
                                                   analog_trace=analog_data, 
                                                   framerate=self.metadata.fps,
                                                   num_of_channels=self.metadata.num_of_channels,
                                                   start_time=self.metadata.start_time,
                                                   timestamps=self.metadata.timestamps)
        self.analog_analyzed.run()
        if self.fluo_trace.shape[0] != 0:
            self.fluo_analyzed = self.analog_analyzed * self.fluo_trace
