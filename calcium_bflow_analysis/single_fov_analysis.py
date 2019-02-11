from collections import namedtuple
from typing import List, Tuple, Union
import itertools

import numpy as np
import pandas as pd
import xarray as xr
import seaborn as sns
import attr
from attr.validators import instance_of
import sys
import pathlib
import matplotlib
import matplotlib.pyplot as plt

sys.path.append(
    str(
        pathlib.Path(
            "/export/home/pblab/data/MatlabCode/PBLabToolkit/CalciumDataAnalysis/python-ca-analysis-bloodflow"
        )
    )
)
from matplotlib import gridspec

from analog_trace import AnalogTraceAnalyzer
from fluo_metadata import FluoMetadata
import dff_tools


@attr.s(slots=True)
class SingleFovParser:
    """ Analyze a single FOV with fluorescent and analog data """

    analog_fname = attr.ib(validator=instance_of(pathlib.Path))
    results_fname = attr.ib(validator=instance_of(pathlib.Path))
    metadata = attr.ib()  # validator=instance_of(FluoMetadata)
    with_analog = attr.ib(default=True, validator=instance_of(bool))
    summarize_in_plot = attr.ib(default=False, validator=instance_of(bool))
    analog_analyzed = attr.ib(init=False, repr=False)  # AnalogTraceAnalyzer instance
    all_fluo_results = attr.ib(init=False, repr=False)  # all data generated by CaImAn
    fluo_trace = attr.ib(init=False, repr=False)  # The specific dF/F trace of that file
    fluo_analyzed = attr.ib(
        init=False
    )  # DataArray with the different slices of run, stim and dF/F

    def parse(self):
        """ Main method to parse a single duo of analog and fluorescent data """
        with np.load(str(self.results_fname), "r+") as self.all_fluo_results:
            self.fluo_trace = self.all_fluo_results["F_dff"]
        try:
            if not self.fluo_trace:  # no cells detected
                self.fluo_trace = np.array([])
        except ValueError:
            pass
        if self.fluo_trace.shape[0] == 0:
            self.fluo_analyzed = None
            return

        if self.with_analog:
            analog_data = pd.read_csv(
                self.analog_fname,
                header=None,
                names=["stimulus", "run"],
                index_col=False,
            )
            self.analog_analyzed = AnalogTraceAnalyzer(
                tif_filename=str(self.analog_fname),
                analog_trace=analog_data,
                framerate=self.metadata.fps,
                start_time=self.metadata.start_time,
                timestamps=self.metadata.timestamps,
            )
            self.analog_analyzed.run()
            self.fluo_analyzed = self.analog_analyzed * self.fluo_trace
        else:
            coords = {
                "neuron": np.arange(self.fluo_trace.shape[0]),
                "time": np.arange(self.fluo_trace.shape[1]) / self.metadata.fps,
                "epoch": ["spont"],
            }
            dims = ["neuron", "time", "epoch"]
            self.fluo_analyzed = xr.DataArray(
                np.atleast_3d(self.fluo_trace), coords=coords, dims=dims
            )
            self.fluo_analyzed.attrs["fps"] = self.metadata.fps
            self.fluo_analyzed.attrs["stim_window"] = 1.5
        if self.summarize_in_plot:
            viz = SingleFovViz(self)
            viz.draw()

    def add_metadata_and_serialize(self):
        """
        Write a full DataArray to disk after parsing the FOV, if it doesn't exist yet.
        The new coordinates order is (epoch, neuron, time, mouse_id, fov, condition).
        """
        try:
            _ = next(
                pathlib.Path(self.metadata.fname).parent.glob(
                    str(self.metadata.fname.name)[:-4] + ".nc"
                )
            )
        except StopIteration:
            try:
                raw_data = self.fluo_analyzed.data
            except AttributeError:
                print("No fluorescent data in this FOV.")
                return
            print("Writing new NetCDF to disk.")
            raw_data = raw_data[..., np.newaxis, np.newaxis, np.newaxis]
            assert len(raw_data.shape) == 6
            coords = {}
            coords["epoch"] = self.fluo_analyzed["epoch"].values
            coords["neuron"] = self.fluo_analyzed["neuron"].values
            coords["time"] = self.metadata.timestamps
            coords["mouse_id"] = np.array([self.metadata.mouse_id])
            coords["fov"] = np.array([self.metadata.fov])
            coords["condition"] = np.array([self.metadata.condition])
            metadata = {
                "day": np.array([self.metadata.day]),
                "fps": self.metadata.fps,
                "stim_window": self.fluo_analyzed.attrs["stim_window"],
            }
            darr = xr.DataArray(
                raw_data, coords=coords, dims=coords.keys(), attrs=metadata
            )
            darr.to_netcdf(
                str(self.metadata.fname)[:-4] + ".nc", mode="w"
            )  # TODO: compress


@attr.s
class SingleFovViz:
    """ Visualization object that uses an existing
    SingleFovParser object as baseline to create figures showing the
    underlying data. This object is related to the functions found
    in ``dff_tools``, but is more specific to a single FOV, i.e.
    a single experiment.

    Usage:
    Main method is ``draw``, that runs all underlying viz scripts
    to generate a big figure with the data hidden inside that FOV.
    """

    fov = attr.ib(validator=instance_of(SingleFovParser))
    save = attr.ib(default=True, validator=instance_of(bool))
    axes_for_dff = attr.ib(default=14, validator=instance_of(int))
    fig = attr.ib(init=False)
    analog_vectors = attr.ib(init=False)
    epochs_to_display = attr.ib(init=False)

    def __attrs_post_init__(self):
        self.analog_vectors = [
            np.nan_to_num(vec)
            for vec in [
                self.fov.analog_analyzed.stim_vec,
                self.fov.analog_analyzed.juxta_vec,
                self.fov.analog_analyzed.run_vec,
            ]
        ]
        if self.fov.analog_analyzed.occluder:
            self.analog_vectors.append(
                np.nan_to_num(self.fov.analog_analyzed.occluder_vec)
            )
        all_epochs = itertools.product(["stand", "run"], ["stim", "spont", "juxta"])
        self.epochs_to_display = ["_".join(epoch) for epoch in all_epochs]

    def draw(self):
        """ Main method of the class.
        Generates a summary figure containing the data inside that
        FOV """
        num_of_axes = 23 if self.fov.with_analog else self.axes_for_dff
        self.fig = plt.figure(figsize=(24, 12))
        if self.fov.analog_analyzed.occluder:
            num_of_axes += 1
        gs = gridspec.GridSpec(num_of_axes, 2)
        scatter_ax = plt.subplot(gs[: self.axes_for_dff, :])
        self._scat_spikes(scatter_ax)
        scatter_ax.xaxis.tick_top()
        scatter_ax.xaxis.set_label_position("top")
        scatter_ax.spines["top"].set_visible(True)
        scatter_ax.spines["bottom"].set_visible(False)
        if self.fov.with_analog:
            gen_patches, colors = self._create_rect_patches(
                self.fov.metadata.fps, self.fov.fluo_trace.shape[1]
            )
            [scatter_ax.add_artist(p) for p in gen_patches]
            cur_used_axes = self._draw_analog_plots(gs, colors)
            auc_axes = plt.subplot(gs[cur_used_axes + 1 :, 0])
            spikes_axes = plt.subplot(gs[cur_used_axes + 1 :, 1])
            self._summarize_stats_in_epochs(auc_axes, spikes_axes)
        if self.save:
            self.fig.savefig(
                str(self.fov.metadata.fname)[:-4] + "_summary.pdf",
                transparent=True,
                dpi=300,
                format="pdf",
            )

    def _create_rect_patches(self, fps: float, height: int):
        """ Creates plt.patches.Rectangle patches to be later added to an existing axis.
        Each type of analog data receives a specified color. """
        all_patches = []
        all_colors = plt.get_cmap("tab20b").colors
        colors: List[Tuple[float, float, float]] = [
            all_colors[7],
            all_colors[11],
            all_colors[15],
            all_colors[19],
        ]  # manually picked due to fitting colors
        vec_and_colors = [
            (vec, colors[idx]) for idx, vec in enumerate(self.analog_vectors)
        ]

        for idx, vec in enumerate(self.analog_vectors):
            diff = np.diff(np.concatenate((vec, [0])))
            starts = np.where(diff == 1)[0]
            ends = np.where(diff == -1)[0]
            # assert len(starts) == len(ends)
            for start, end in zip(starts, ends):
                all_patches.append(
                    matplotlib.patches.Rectangle(
                        (start / fps, 0),
                        width=(end - start) / fps,
                        height=height,
                        facecolor=colors[idx],
                        alpha=0.5,
                        edgecolor="None",
                    )
                )
        return all_patches, colors

    def _scat_spikes(self, ax):
        """ Plots all dF/F traces and spikes on a given axes """
        spikes = dff_tools.locate_spikes_peakutils(
            self.fov.fluo_trace, self.fov.metadata.fps
        )
        time_vec = np.arange(self.fov.fluo_trace.shape[1]) / self.fov.metadata.fps
        dff_tools.scatter_spikes(
            self.fov.fluo_trace, spikes, downsample_display=1, time_vec=time_vec, ax=ax
        )

    def _draw_analog_plots(self, gs: gridspec.GridSpec, colors):
        """ For each available analog data vector add its
        data to the screen """
        labels = ["Air puff", "Juxtaposed\npuff", "Run time", "CCA\nocclusion times"]

        for idx, (label, data, color) in enumerate(
            zip(labels, self.analog_vectors, colors), self.axes_for_dff
        ):
            cur_ax = plt.subplot(gs[idx, :])
            cur_ax.plot(data, color=color)
            cur_ax.set_ylabel(label, rotation=45, fontsize=8)
            cur_ax.set_xlabel("")
            cur_ax.set_xticks([])
            cur_ax.set_ylim(-0.05, 1.05)
            cur_ax.set_yticks([])
            cur_ax.spines["top"].set_visible(False)
            cur_ax.spines["bottom"].set_visible(False)
            cur_ax.spines["right"].set_visible(False)
            cur_ax.spines["left"].set_visible(False)
            cur_ax.set_frame_on(False)

        return idx

    def _summarize_stats_in_epochs(self, ax_auc: plt.Axes, ax_spikes: plt.Axes):
        """ Add axes to the main plot showing the dF/F statistics in the
        different epochs """

        df_auc = pd.DataFrame(
            np.full((400, len(self.epochs_to_display)), np.nan),
            columns=self.epochs_to_display,
        )
        df_spikes = df_auc.copy()
        for epoch in self.epochs_to_display:
            cur_data = filter_da(self.fov.fluo_analyzed, epoch=epoch)
            if cur_data.shape == (0,):
                continue
            auc = dff_tools.calc_auc(cur_data)
            df_auc[epoch][: len(auc)] = auc
            spikes = dff_tools.calc_mean_spike_num(cur_data, fps=self.fov.metadata.fps)
            df_spikes[epoch][: len(spikes)] = spikes

        sns.boxenplot(data=df_auc, ax=ax_auc)
        sns.boxenplot(data=df_spikes, ax=ax_spikes)
        for ax in [ax_auc, ax_spikes]:
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.set_xlabel("Epoch")
        ax_auc.set_ylabel("AUC")
        ax_spikes.set_ylabel("Spikes per second")


def filter_da(
    data: xr.DataArray, epoch: str, condition: Union[str, None] = None
) -> np.ndarray:
    """ Filter a DataArray by the given condition and epoch.
         Returns a numpy array in the shape of cells x time """
    if condition:
        selected = np.squeeze(
            data.sel(condition=condition, epoch=epoch, drop=True).values
        )
    else:
        selected = np.squeeze(data.sel(epoch=epoch, drop=True).values)
    selected = np.atleast_2d(selected)
    relevant_idx = np.isfinite(selected).any(axis=1)
    num_of_cells = relevant_idx.sum()
    if num_of_cells > 0:
        selected = selected[relevant_idx].reshape((num_of_cells, -1))
        return selected
    return np.array([])


if __name__ == "__main__":
    home = pathlib.Path("/export/home/pblab/")
    # home = pathlib.Path('/')
    folder = home / pathlib.Path(
        r"data/David/NEW_crystal_skull_TAC_161018/DAY_14_ALL/147_HYPER_DAY_14/"
    )
    fov = 3
    analog_fname = next(folder.glob(f"*FOV_{fov}*analog.txt"))
    results_fname = next(folder.glob(f"*FOV_{fov}*results.npz"))
    orig_fname = next(folder.glob(f"*FOV_{fov}*01.tif"))
    meta = FluoMetadata(orig_fname)
    meta.get_metadata()
    sfov = SingleFovParser(analog_fname, results_fname, meta, summarize_in_plot=True)
    sfov.parse()
    plt.show()
