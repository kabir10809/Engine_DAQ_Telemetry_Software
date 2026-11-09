"""Export recorded runs and import outside data.

Multi-rate channels are placed on the union of their timestamps with blanks
where a channel has no sample. Nothing is resampled or interpolated.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .. import APP_VERSION, RUN_SCHEMA, RUNTIME
from ..util import iso_time, new_id, opt, sha256, to_list
from .store import RunRecorder

DATA_FORMATS = ("csv", "txt", "xlsx", "xls", "json", "xml", "mat", "h5", "hdf5", "parquet")
FIGURE_FORMATS = ("pdf", "png", "svg", "jpg", "jpeg")


class FormatError(ValueError):
    pass


def _valid_name(text: str) -> str:
    return re.sub(r"\W", "_", text)


def wide_table(reader, data, ids, content="processed", include_flags=False, calculated=None) -> pd.DataFrame:
    all_t = np.unique(np.concatenate([data[i]["t"] for i in ids])) if ids else np.array([])
    table = pd.DataFrame({"t_posix_s": all_t, "time_utc": [iso_time(float(t)) for t in all_t], "t_run_s": all_t - reader.start_t})
    for channel_id in ids:
        series = data[channel_id]
        locations = np.searchsorted(all_t, series["t"])

        def spread(values):
            column = np.full(all_t.size, np.nan)
            column[locations] = values
            return column

        if content in ("processed", "both"):
            table[f'{channel_id}_{_valid_name(reader.channel(channel_id).get("unit", "-"))}'] = spread(series["value"])
        if content in ("raw", "both"):
            table[f"{channel_id}_raw"] = spread(series["raw"])
        if include_flags:
            table[f"{channel_id}_flags"] = spread(series["flags"])
    if calculated is not None:
        merged = table[["t_posix_s"]].merge(calculated.rename(columns={"t": "t_posix_s"}), on="t_posix_s", how="left")
        for name in calculated.columns:
            if name != "t" and pd.api.types.is_numeric_dtype(calculated[name]):
                table[f"calc_{name}"] = merged[name].to_numpy()
    return table


def _metadata(reader) -> dict:
    m = reader.manifest
    return {"runtime": m.get("runtime", ""), "appVersion": m.get("appVersion", ""),
            "configSha256": opt(m, "configSha256", ""), "startT": reader.start_t}


def export(reader, path, channels=None, time_range=None, content="processed", include_flags=False, calculated=None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = list(channels) if channels else reader.channel_ids()
    table = wide_table(reader, reader.read(ids, time_range), ids, content, include_flags, calculated)
    extension = path.suffix.lower().lstrip(".")
    if extension == "csv":
        table.to_csv(path, index=False)
    elif extension == "txt":
        table.to_csv(path, index=False, sep="\t")
    elif extension in ("xlsx", "xls"):
        limit = 1048575 if extension == "xlsx" else 65535
        if len(table) > limit:
            raise FormatError(f"{len(table)} rows exceed the .{extension} limit of {limit}. Select a shorter range or use another format.")
        _write_excel(table, path, extension)
    elif extension == "json":
        document = {"metadata": _metadata(reader), "columns": table.astype(object).where(table.notna(), None).to_dict(orient="list")}
        path.write_text(json.dumps(document), encoding="utf-8")
    elif extension == "xml":
        table.to_xml(path, index=False, root_name="EngineDaqExport", row_name="sample", parser="lxml")
    elif extension == "mat":
        from scipy.io import savemat
        columns = {name[:31]: table[name].to_numpy() for name in table.columns if name != "time_utc"}
        savemat(path, {"export": {"metadata": _metadata(reader), "columns": columns}}, do_compression=True)
    elif extension in ("h5", "hdf5"):
        import h5py
        with h5py.File(path, "w") as file:
            for name in table.columns:
                if name != "time_utc":
                    file.create_dataset(f"data/{name}", data=table[name].to_numpy(), compression="gzip")
            for key, value in _metadata(reader).items():
                file.attrs[key] = value
    elif extension == "parquet":
        table.to_parquet(path, index=False)
    else:
        raise FormatError(f'Unsupported export format ".{extension}".')


def _write_excel(table, path, extension) -> None:
    if extension == "xlsx":
        table.to_excel(path, index=False)
        return
    import xlwt
    book = xlwt.Workbook()
    sheet = book.add_sheet("data")
    for c, name in enumerate(table.columns):
        sheet.write(0, c, name)
        for r, value in enumerate(table[name].tolist(), start=1):
            if not (isinstance(value, float) and np.isnan(value)):
                sheet.write(r, c, value)
    book.save(str(path))


def export_figure(reader, path, channels=None, time_range=None, title="") -> None:
    """Plot selected channels to PDF, PNG, SVG or JPEG."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    extension = path.suffix.lower().lstrip(".")
    if extension not in FIGURE_FORMATS:
        raise FormatError(f'Unsupported figure format ".{extension}".')
    ids = list(channels) if channels else reader.channel_ids()[:6]
    data = reader.read(ids, time_range)
    figure = Figure(figsize=(12, max(3.0, 2.0 * len(ids))), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(len(ids), 1, sharex=True, squeeze=False)[:, 0]
    if title:
        figure.suptitle(title)
    for ax, channel_id in zip(axes, ids):
        info = reader.channel(channel_id)
        ax.plot(data[channel_id]["t"] - reader.start_t, data[channel_id]["value"], linewidth=1)
        ax.set_ylabel(f'{info.get("name", channel_id)} [{info.get("unit", "-")}]')
        ax.grid(True, alpha=0.4)
    axes[-1].set_xlabel("Time since run start [s]")
    figure.savefig(path, dpi=200)


def preview(path) -> pd.DataFrame:
    """Source file as a table, for column mapping."""
    path = Path(path)
    extension = path.suffix.lower()
    if extension == ".csv":
        return pd.read_csv(path)
    if extension == ".txt":
        return pd.read_csv(path, sep=None, engine="python")
    if extension in (".xlsx", ".xls"):
        return pd.read_excel(path)
    if extension == ".parquet":
        return pd.read_parquet(path)
    if extension == ".json":
        document = json.loads(path.read_text(encoding="utf-8"))
        return pd.DataFrame(document.get("columns", document))
    if extension == ".mat":
        return _from_mat(path)
    if extension in (".h5", ".hdf5"):
        return _from_hdf5(path)
    raise FormatError(f'Unsupported import format "{path.suffix}".')


def _from_mat(path) -> pd.DataFrame:
    from scipy.io import loadmat
    contents = {k: v for k, v in loadmat(path, simplify_cells=True).items() if not k.startswith("__")}
    if "export" in contents:
        return pd.DataFrame(contents["export"]["columns"])
    value = next(iter(contents.values()))
    return pd.DataFrame(value) if not isinstance(value, np.ndarray) or value.ndim == 2 else pd.DataFrame({"value": value})


def _from_hdf5(path) -> pd.DataFrame:
    import h5py
    columns = {}
    with h5py.File(path, "r") as file:
        file.visititems(lambda name, item: columns.__setitem__(name.rsplit("/", 1)[-1], np.ravel(item[()]))
                        if isinstance(item, h5py.Dataset) else None)
    return pd.DataFrame(columns)


def ingest(path, mapping: dict, destination) -> Path:
    """Create a run from a file, keeping the source file's metadata.

    mapping: time and timeKind ("seconds", "posix", "datetime"), or sampleRateHz
    when the file has no time column, and channels: list of column, id, name,
    unit, quantity. Values are stored as both raw and value, with flags 0,
    because the file records no quality information.
    """
    path = Path(path)
    table = preview(path)
    if "sampleRateHz" in mapping:
        t = np.arange(len(table)) / float(mapping["sampleRateHz"])
    else:
        column = table[mapping["time"]]
        if mapping["timeKind"] == "datetime":
            t = pd.to_datetime(column, utc=True).astype("int64").to_numpy() / 1e9
        else:
            t = column.to_numpy(float)
    channels = to_list(mapping["channels"])
    described = [{"id": c["id"], "name": opt(c, "name", c["id"]), "unit": opt(c, "unit", "-"),
                  "quantity": opt(c, "quantity", "Dimensionless"), "kind": "imported", "sourceColumn": c["column"]}
                 for c in channels]
    stat = path.stat()
    manifest = {"schemaVersion": RUN_SCHEMA, "runtime": RUNTIME, "appVersion": APP_VERSION, "startT": float(np.nanmin(t)),
                "endT": float(np.nanmax(t)), "source": "import", "channels": described,
                "sourceFile": {"name": path.name, "path": str(path), "bytes": stat.st_size, "modified": stat.st_mtime,
                               "sha256": sha256(path.read_bytes()), "columns": [str(c) for c in table.columns]},
                "importedAt": new_id("import")}
    recorder = RunRecorder(destination, manifest)
    for k, channel in enumerate(channels, start=1):
        column = table[channel["column"]].to_numpy(float)
        ok = ~np.isnan(t) & ~np.isnan(column)
        recorder.append(k, t[ok], column[ok], column[ok], np.zeros(int(ok.sum())))
    recorder.close(float(np.nanmax(t)))
    return Path(destination)
