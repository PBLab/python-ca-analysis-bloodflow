import attr
from attr.validators import instance_of
import numpy as np
import pandas as pd
import pathlib
import sys
import peakutils
from statsmodels.stats.multicomp import MultiComparison
from statsmodels.stats.libqsturng import psturng
import scipy.stats
import matplotlib.pyplot as plt
from matplotlib import patches


@attr.s(slots=True)
class VascOccAnalysis:
    foldername = attr.ib(validator=instance_of(str))
    glob = attr.ib(default='*results.npz', validator=instance_of(str))
    fps = attr.ib(default=15.24, validator=instance_of(float))
    frames_before_stim = attr.ib(default=1000)
    len_of_epoch_in_frames = attr.ib(default=1000)
    invalid_cells = attr.ib(default=[], validator=instance_of(list))
    dff = attr.ib(init=False)
    all_mice = attr.ib(init=False)
    split_data = attr.ib(init=False)
    all_spikes = attr.ib(init=False)

    def run(self):
        files = self.__find_all_files()
        self.__calc_dff(files)
        before, during, after = self.__find_spikes()
        self.__calc_firing_rate(before, during, after)
        self.__scatter_spikes()
        self.__rolling_window()
        return self.dff

    def __find_all_files(self):
        """
        Locate all fitting files in the folder
        """
        self.all_mice = []
        files = pathlib.Path(self.foldername).rglob(self.glob)
        print("Found the following files:")
        for file in files:
            print(file)
            self.all_mice.append(str(file))
        files = pathlib.Path(self.foldername).rglob(self.glob)
        return files

    def __calc_dff(self, files):
        # sys.path.append(r'/data/Hagai/Multiscaler/code_for_analysis')
        import caiman_funcs_for_comparison

        coords = {'mouse': self.all_mice, }
        all_data = []
        for file in files:
            data = np.load(file)
            print(f"Analyzing {file}...")
            all_data.append(caiman_funcs_for_comparison.detrend_df_f_auto(data['A'], data['b'], data['C'],
                                                                          data['f'], data['YrA']))
        self.dff = np.concatenate(all_data)

    def __find_spikes(self):
        idx_section1 = []
        idx_section2 = []
        idx_section3 = []
        thresh = 0.65
        min_dist = 7
        self.all_spikes = np.zeros_like(self.dff)

        for row, cell in enumerate(self.dff):
            # idx1 = peakutils.indexes(cell[:self.len_of_epoch_in_frames], thres=thresh, min_dist=min_dist)
            # idx2 = peakutils.indexes(cell[self.len_of_epoch_in_frames:2*self.len_of_epoch_in_frames],
            #                          thres=thresh, min_dist=min_dist)
            # idx3 = peakutils.indexes(cell[2*self.len_of_epoch_in_frames:], thres=thresh, min_dist=min_dist)
            # idx_section1.append(idx1)
            # idx_section2.append(idx2)
            # idx_section3.append(idx3)
            # idxs = np.concatenate((idx1,
            #                        idx2 + self.len_of_epoch_in_frames,
            #                        idx3 + (2*self.len_of_epoch_in_frames)))
            # self.all_spikes[row, idxs] = 1
            idx = peakutils.indexes(cell, thres=thresh, min_dist=min_dist)
            self.all_spikes[row, idx] = 1
            after_stim = self.frames_before_stim + self.len_of_epoch_in_frames
            idx_section1.append(idx[idx < self.frames_before_stim])
            idx_section2.append(idx[(idx >= self.frames_before_stim) & (idx < (after_stim))])
            idx_section3.append(idx[idx >= (after_stim)])
        return idx_section1, idx_section2, idx_section3,

    def __calc_firing_rate(self, idx_section1, idx_section2, idx_section3):
        """
        Sum all indices of peaks to find the average firing rate of cells in the three epochs
        :param idx_section1:
        :param idx_section2:
        :param idx_section3:
        :return:
        """
        df = pd.DataFrame(columns=['before', 'during', 'after'], index=np.arange(len(idx_section1)))
        df['before'] = [len(cell) for cell in idx_section1]
        df['during'] = [len(cell) for cell in idx_section2]
        df['after'] = [len(cell) for cell in idx_section3]

        # Remove silent cells from comparison
        df.drop(self.invalid_cells, inplace=True)
        self.split_data = df.stack()
        mc = MultiComparison(self.split_data.values, self.split_data.index.get_level_values(1).values)
        res = mc.tukeyhsd()
        print(res)
        print("P-values:", psturng(np.abs(res.meandiffs / res.std_pairs), len(res.groupsunique), res.df_total))
        print(self.split_data.mean(level=1))

    def __scatter_spikes(self):
        """
        Show a scatter plot of spikes in the three epochs
        :param before:
        :param during:
        :param after:
        :return:
        """
        x, y = np.nonzero(self.all_spikes)
        fig, ax = plt.subplots()
        ax.plot((self.dff + np.arange(self.dff.shape[0])[:, np.newaxis]).T)
        peakvals = self.dff * self.all_spikes
        peakvals[peakvals == 0] = np.nan
        ax.plot((peakvals + np.arange(self.dff.shape[0])[:, np.newaxis]).T, 'r.', linewidth=0.1)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.set_xlabel('Time (frames)')
        ax.set_ylabel('Cell ID')
        p = patches.Rectangle((self.frames_before_stim, 0), width=self.len_of_epoch_in_frames,
                              height=self.dff.shape[0], color='red', alpha=0.3, edgecolor='None')
        ax.add_artist(p)
        plt.savefig('spike_scatter.pdf', transparent=True)

    def __rolling_window(self):
        mean_spike = pd.DataFrame(self.all_spikes.mean(axis=0))
        mean_spike['x'] = np.arange(mean_spike.shape[0])/self.fps
        ax = mean_spike.rolling(window=int(self.fps)).mean().plot(x='x')
        ax.set_xlabel('Time (sec)')
        ax.set_ylabel('Mean Spike Rate')
        ax.set_title('Rolling mean (0.91 sec window length)')
        ax.plot(np.arange(self.frames_before_stim, self.frames_before_stim + self.len_of_epoch_in_frames)/self.fps,
                np.full(self.len_of_epoch_in_frames, 0.01), 'r')
        plt.savefig('mean_spike_rate.pdf', transparent=True)


if __name__ == '__main__':
    vasc = VascOccAnalysis(foldername=r'/data/David/vasc_occ_air_puff_010418',
                           glob=r'fov*_2000fr*results.npz', frames_before_stim=2000,
                           len_of_epoch_in_frames=2000, fps=15.24,
                           invalid_cells=[77, 76, 91, 83, 87, 95, 7])
    vasc.run()
    plt.show(block=False)