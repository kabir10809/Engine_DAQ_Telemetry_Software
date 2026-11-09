"""National Instruments DAQ through nidaqmx. Needs NI's own driver installed as
well. See the compatibility matrix for its verification status."""
import numpy as np

from edaq.hw.driver import Driver, empty_status, make_block, make_candidate
from edaq.util import opt, to_list


class NiDaqDriver(Driver):
    def __init__(self):
        super().__init__()
        self._task = None
        self._packets_ok = 0
        self._error = ""

    @staticmethod
    def manifest():
        return {"id": "ni_daqmx", "name": "NI-DAQmx", "version": "1.0.0", "interfaces": ["NI-DAQmx"], "platforms": ["windows", "linux"], "requires": ["nidaqmx"], "configSchema": [
            {"name": "sampleRateHz", "label": "Sample rate (Hz)", "type": "number", "default": 100, "help": ""},
            {"name": "channels", "label": "Channels", "type": "table:hw,device,channel,measurement,rangeLow,rangeHigh,thermocoupleType", "required": True,
             "help": "measurement: Voltage or Thermocouple."}]}

    @staticmethod
    def discover():
        import nidaqmx.system
        return [make_candidate("ni_daqmx", "NI-DAQmx", device.name, "National Instruments", device.product_type, str(device.dev_serial_num), "device_identified", "")
                for device in nidaqmx.system.System.local().devices]

    def connect(self):
        import nidaqmx
        from nidaqmx.constants import AcquisitionType, ThermocoupleType
        rows = to_list(self.settings["channels"])
        self._names = [str(r["hw"]) for r in rows]
        task = nidaqmx.Task()
        for row in rows:
            physical = f'{row["device"]}/{row["channel"]}'
            if row["measurement"] == "Thermocouple":
                task.ai_channels.add_ai_thrmcpl_chan(physical, thermocouple_type=ThermocoupleType[str(opt(row, "thermocoupleType", "K"))])
            else:
                task.ai_channels.add_ai_voltage_chan(physical, min_val=float(opt(row, "rangeLow", -10)), max_val=float(opt(row, "rangeHigh", 10)))
        task.timing.cfg_samp_clk_timing(float(self.settings["sampleRateHz"]), sample_mode=AcquisitionType.CONTINUOUS)
        task.start()
        self._task = task

    def identify(self):
        return {"vendor": "National Instruments", "model": str(to_list(self.settings["channels"])[0]["device"]), "serial": "", "firmware": "", "evidence": "Device name from NI-DAQmx."}

    def discover_channels(self):
        return [{"hw": name, "unit": "", "sampleRateHz": float(self.settings["sampleRateHz"]), "description": "NI input"} for name in self._names]

    def read(self, t_now):
        from nidaqmx.constants import READ_ALL_AVAILABLE
        try:
            data = np.atleast_2d(self._task.read(number_of_samples_per_channel=READ_ALL_AVAILABLE, timeout=0))
        except Exception as error:
            self._error = str(error)
            raise
        n = data.shape[1]
        times = t_now - np.arange(n - 1, -1, -1) / float(self.settings["sampleRateHz"])
        self._packets_ok += n
        return make_block(self._names, [times] * len(self._names), list(data))

    def status(self):
        st = empty_status()
        st.update(connected=self._task is not None, packetsOk=self._packets_ok)
        if self._error:
            st.update(instrument="fault", instrumentDetail=self._error)
        return st

    def disconnect(self):
        if self._task is not None:
            self._task.close()
            self._task = None
