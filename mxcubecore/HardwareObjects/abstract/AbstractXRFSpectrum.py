# encoding: utf-8
#
#  Project: MXCuBE
#  https://github.com/mxcube
#
#  This file is part of MXCuBE software.
#
#  MXCuBE is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  MXCuBE is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Lesser General Public License for more details.
#
#  You should have received a copy of the GNU General Lesser Public License
#  along with MXCuBE. If not, see <http://www.gnu.org/licenses/>.

"""Abstract XRF spectrum class. Complient with queue_entry/xrf_spectrum.py
Define methods:
 - start_xrf_spectrum, execute_xrf_spectrum, spectrum_store_lims,
   spectrum_command_finished, spectrum_command_failed,
   spectrum_command_aborted, spectrum_status_change
Defines hooks for specific implementation:
 - _execite_xrf_scan, spectrum_analyse, 

Emit signals:
 - xrfSpectrumStarted
 - xrfSpectrumFinished
 - xrfSpectrumFailed
 - xrfSpectrumStatusChanged
"""

import abc
import logging
import os
import time
import gevent
from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore import HardwareRepository as HWR

__copyright__ = """ Copyright © 2010-2023 by the MXCuBE collaboration """
__license__ = "LGPLv3+"


class AbstractXRFSpectrum(HardwareObject):
    """Abstract XRFSpectrum procedure"""

    __metaclass__ = abc.ABCMeta

    def __init__(self, name):
        super().__init__(name)
        self.scanning = None
        self.lims = None
        self.spectrum_info_dict = {}
        self.default_integration_time = None
        self.spectrum_running = None

    def init(self):
        """Initialisation"""
        self.default_integration_time = self.get_property("default_integration_time", 3)
        self.lims = HWR.beamline.lims
        if not self.lims:
            logging.getLogger().warning("XRFSpectrum: no lims set")

    def start_xrf_spectrum(
        self,
        integration_time=None,
        data_dir=None,
        archive_dir=None,
        prefix=None,
        session_id=None,
        blsample_id=None,
    ):
        """Start the procedure. Called by the queu_model.
        Args:
            integration_time(float): Inregration time [s].
            data_dir (str): Directory to save the data (full path).
            archive_dir (str): Directory to save the archive data (full path).
            prefix (str): File prefix
            session_id (int): Session ID number (from ISpyB)
            blsample_id (int): Sample ID number (from ISpyB)
        """
        self.spectrum_info_dict = {"sessionId": session_id, "blSampleId": blsample_id}

        integration_time = integration_time or self.default_integration_time
        self.spectrum_info_dict["exposureTime"] = integration_time

        # Create the data and the archive directory (if needed) and files
        if data_dir:
            if not self.create_directory(data_dir):
                return False
            filename = self.get_filename(data_dir, prefix)
            self.spectrum_info_dict["filename"] = filename + ".dat"
        if archive_dir:
            if not self.create_directory(archive_dir):
                return False
            filename = self.get_filename(archive_dir, prefix)
            self.spectrum_info_dict["scanFileFullPath"] = filename + ".dat"
            self.spectrum_info_dict["jpegScanFileFullPath"] = filename + ".png"
            self.spectrum_info_dict["annotatedPymcaXfeSpectrum"] = filename + ".html"
            self.spectrum_info_dict["fittedDataFileFullPath"] = filename + "_peaks.csv"

        self.spectrum_info_dict["startTime"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.spectrum_running = True
        self.emit("xrfSpectrumStarted", ())
        gevent.spawn(self.execute_xrf_spectrum())
        return True

    def execute_xrf_spectrum(self):
        """Do the acquisition"""
        try:
            if self._execute_xrf_spectrum():
                self.spectrum_command_finished()
        except RuntimeError as err:
            msg = "XRFSpectrum: could not acquire spectrum"
            logging.getLogger("user_level_log").exception(f"{msg}, {err}")
            self.spectrum_status_change(msg)

    def _execute_xrf_spectrum(self):
        """Specific xrf acquisition procedure"""

    def create_directory(self, directory):
        """Create a directory, if needed.
        Args:
            directory (str): Directory to save the data (full path).
        Returns:
           (bool): Tue if directory created or already exists, False if error.
        """
        if not os.path.isdir(directory):
            msg = f"XRFSpectrum: directory creating {directory}"
            try:
                if not os.path.exists(directory):
                    logging.getLogger("user_level_log").debug(msg)
                    os.makedirs(directory)
                return True
            except OSError as err:
                logging.getLogger().error(msg, err)
                self.spectrum_status_change("Error creating directory")
                self.spectrum_command_aborted()
                return False
        return True

    def get_filename(self, directory, prefix):
        """Create file template.
        Args:
            directory(str): directory name (full path)
        Returns:
            (str): File template
        """
        _pattern = f"{prefix}_{time.strftime('%d_%b_%Y')}_%02d_xrf"
        filename_pattern = os.path.join(directory, _pattern)
        filename = filename_pattern % 1
        # fileprefix = _pattern % 1

        i = 2
        while os.path.isfile(filename):
            filename = filename_pattern % i
            # fileprefix = _pattern % i
            i = i + 1

        return filename

    def spectrum_status_change(self, status_msg):
        """Emit the signal xrfSpectrumStatusChanged with appropriate message.
        Args:
            status_msg(str): Message to send.
        """
        self.emit("xrfSpectrumStatusChanged", (status_msg,))

    def spectrum_command_finished(self):
        """Actions to do if spectrum acquired."""
        self.spectrum_info_dict["endTime"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.spectrum_running = False
        self.spectrum_info_dict[
            "beamTransmission"
        ] = HWR.beamline.transmission.get_value()
        if HWR.beamline.energy:
            self.spectrum_info_dict["energy"] = HWR.beamline.energy.get_value()
        if HWR.beamline.flux:
            self.spectrum_info_dict["flux"] = HWR.beamline.flux.get_value()
        if HWR.beamline.beam:
            size = HWR.beamline.beam.get_value()
            self.spectrum_info_dict["beamSizeHorizontal"] = size[0]
            self.spectrum_info_dict["beamSizeVertical"] = size[1]
        self.spectrum_analyse()
        if self.lims:
            self.spectrum_store_lims()

        self.update_state(self.STATES.READY)

    def spectrum_analyse(self):
        """Get the spectrum data. Do analysis and save fitted data.
        The method has to be implemented as specific for each site.
        """

    def spectrum_command_aborted(self):
        """Spectrum aborted actions"""
        self.spectrum_running = False
        self.emit("xrfSpectrumFailed", ())
        self.update_state(self.STATES.READY)

    def spectrum_command_failed(self):
        """Spectrum failed actions"""
        self.spectrum_info_dict["endTime"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.spectrum_running = False
        if self.lims:
            self.spectrum_store_lims()
        self.emit("xrfSpectrumFailed", ())
        # self.update_state(self.STATES.READY)

    def spectrum_store_lims(self):
        """Store the data in lims, according to the existing data model."""
        if self.spectrum_info_dict["sessionId"]:
            self.lims.storeXfeSpectrum(self.spectrum_info_dict)
