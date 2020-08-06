import pathlib
import json

from magicgui import magicgui, event_loop
import cv2
import tifffile
import numpy as np
import matplotlib.pyplot as plt

from calcium_bflow_analysis.dff_analysis_and_plotting import plot_cells_and_traces

# linux only
CACHE_FOLDER = pathlib.Path.home() / pathlib.Path('.cache/ca_analysis_bloodflow')
CACHE_FOLDER.mkdir(mode=0o777, parents=True, exist_ok=True)


def write_to_cache(foldername, data: dict):
    if not foldername.exists():
        return
    filename = foldername / 'overlay_channels.json'
    try:
        with open(filename, 'w') as f:
            json.dump(data, f)
    except (FileNotFoundError, PermissionError) as e:
        print(repr(e))


def read_from_cache(foldername) -> dict:
    if not foldername.exists():
        return
    filename = foldername / 'overlay_channels.json'
    data = None
    try:
        with open(filename) as f:
            data = json.load(f)
    except (FileNotFoundError, PermissionError):
        pass
    return data


def _find_start_end_frames(inp: str):
    splitted = inp.split(',')
    if len(splitted) == 1:
        return slice(None, None)
    try:
        start = int(splitted[0])
    except ValueError:
        start = None
    try:
        end = int(splitted[1])
    except ValueError:
        end = None
    return slice(start, end)


def _normalize_arrays(ch1: np.ndarray, ch2: np.ndarray):
    if ch1.shape == ch2.shape:
        return ch1, ch2
    if ch1.shape[0] < ch2.shape[0]:
        ch2 = cv2.resize(ch2, (ch1.shape))
    else:
        ch1 = cv2.resize(ch1, ch2.shape)
    return ch1, ch2


@magicgui(call_button="Show", layout="form", ch1_fname={'fixedWidth': 1000})
def overlay_channels_and_show_traces(ch1_fname: str = ".tif", ch1_frames: str = "", ch2_fname: str = ".tif", ch2_frames: str = "", results_fname: str = ".npz", cell_radius: int = 6):
    ch1_fname = pathlib.Path(ch1_fname)
    if not ch1_fname.exists():
        return "Channel 1 path doesn't exist"
    ch2_fname = pathlib.Path(ch2_fname)
    if not ch1_fname.exists():
        return "Channel 2 path doesn't exist"
    results_fname = pathlib.Path(results_fname)
    if not results_fname.exists():
        return "Results path doesn't exist"
    ch1_slice = _find_start_end_frames(ch1_frames)
    ch2_slice = _find_start_end_frames(ch2_frames)
    write_to_cache(CACHE_FOLDER, {'ch1_fname': str(ch1_fname), 'ch1_frames': ch1_frames, 'ch2_fname': str(ch2_fname), 'ch2_frames': ch2_frames, 'results_fname': str(results_fname), 'cell_radius': cell_radius})
    print("reading files...")
    ch1 = tifffile.imread(str(ch1_fname))[ch1_slice].mean(axis=0)
    ch2 = tifffile.imread(str(ch2_fname))[ch2_slice].mean(axis=0)
    ch1, ch2 = _normalize_arrays(ch1, ch2)
    print("finished reading tiffs")
    im = cv2.addWeighted(ch1, 0.5, ch2, 0.5, 0)
    new_fname = str(ch1_fname.parent / ('combined_' + ch1_fname.stem + '_' + ch2_fname.stem + '.tif'))
    tifffile.imwrite(new_fname, np.stack([ch1, ch2]))
    plot_cells_and_traces.show_side_by_side([im], [results_fname], None, cell_radius)
    plt.show(block=False)
    return new_fname


if __name__ == '__main__':
    data = read_from_cache(CACHE_FOLDER)
    with event_loop():
        gui = overlay_channels_and_show_traces.Gui(show=True)
        if data:
            gui.ch1_fname = data['ch1_fname']
            gui.ch2_fname = data['ch2_fname']
            gui.results_fname = data['results_fname']
            gui.cell_radius = data['cell_radius']
            gui.ch1_frames = data['ch1_frames']
            gui.ch2_frames = data['ch2_frames']
        gui.called.connect(lambda x: gui.set_widget("Message:", str(x), position=-1))

