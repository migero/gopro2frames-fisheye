#!/usr/bin/env python3
import sys
import os
import subprocess
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QGridLayout,
    QCheckBox, QDoubleSpinBox, QSpinBox, QFileDialog, QComboBox,
    QMessageBox, QTextEdit, QProgressBar, QSizePolicy, QApplication
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
import json
import threading
import time

# Import the existing modules
from gfmhelper import GoProFrameMakerHelper
from gfmmain import GoProFrameMaker


class ProcessingThread(QThread):
    """Thread for running the actual processing to keep GUI responsive"""
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.is_cancelled = False
    
    def run(self):
        # Convert args dict to sys.argv format
        sys.argv = ['gfm.py'] + self.build_argv_from_args()
        
        # Run the original gfm.py logic
        try:
            # Import and run the module
            import gfm
            # The gfm.py module will execute when imported with the right sys.argv
            result = 0
        except SystemExit as e:
            result = e.code if e.code is not None else 0
        except Exception as e:
            self.progress_signal.emit(f"Error during processing: {str(e)}")
            result = 1
        
        self.finished_signal.emit(result)
    
    def build_argv_from_args(self):
        """Build command line arguments from GUI args"""
        argv = []
        
        # Required input argument
        if 'input' in self.args:
            argv.extend(self.args['input'])
        
        # Optional arguments
        if self.args.get('fisheye_width') is not None:
            argv.extend(['--fisheye-width', str(self.args['fisheye_width'])])
        
        if self.args.get('frame_rate') is not None:
            argv.extend(['--frame-rate', str(self.args['frame_rate'])])
        
        if self.args.get('max_seconds') is not None:
            argv.extend(['--max-seconds', str(self.args['max_seconds'])])
        
        if self.args.get('startf') is not None:
            argv.extend(['--startf', str(self.args['startf'])])
        
        if self.args.get('endf') is not None:
            argv.extend(['--endf', str(self.args['endf'])])
        
        if self.args.get('detect_sharpness'):
            argv.append('--detect-sharpness')
        
        if self.args.get('crop_size') is not None:
            argv.extend(['--crop-size', str(self.args['crop_size'])])
        
        if self.args.get('threshold') is not None:
            argv.extend(['--threshold', str(self.args['threshold'])])
        
        if self.args.get('fisheye_only'):
            argv.append('--fisheyeonly')
        
        if self.args.get('e360_only'):
            argv.append('--360only')
        
        # These are only available in config-less mode
        if not GoProFrameMakerHelper.getConfig()['status']:
            if self.args.get('ffmpeg_path'):
                argv.extend(['--ffmpeg-path', self.args['ffmpeg_path']])
            
            if self.args.get('quality') is not None:
                argv.extend(['--quality', str(self.args['quality'])])
            
            if self.args.get('debug'):
                argv.append('--debug')
            
            if self.args.get('lut_file'):
                argv.extend(['--lut-file', self.args['lut_file']])
        
        return argv


class GoProFrameMakerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GoPro Frame Maker GUI")
        self.setGeometry(100, 100, 800, 700)
        
        # Initialize config status
        self.cfg = GoProFrameMakerHelper.getConfig()
        self.config_available = self.cfg['status']
        
        # Thread for processing
        self.processing_thread = None
        
        # Create the UI
        self.init_ui()
        
    def init_ui(self):
        """Initialize the user interface"""
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Title
        title_label = QLabel("GoPro Frame Maker GUI")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # Create scrollable area for all controls
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(15)
        
        # Input Section
        input_group = self.create_input_group()
        scroll_layout.addWidget(input_group)
        
        # Processing Options Section
        options_group = self.create_options_group()
        scroll_layout.addWidget(options_group)
        
        # Advanced Options Section
        advanced_group = self.create_advanced_group()
        scroll_layout.addWidget(advanced_group)
        
        # Progress Section
        progress_group = self.create_progress_group()
        scroll_layout.addWidget(progress_group)
        
        # Add scroll widget to main layout
        main_layout.addWidget(scroll_widget)
        
        # Button section
        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Start Processing")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch()
        
        main_layout.addLayout(button_layout)
        
        # Connect buttons
        self.start_button.clicked.connect(self.start_processing)
        self.cancel_button.clicked.connect(self.cancel_processing)
        
        # Initialize args dictionary
        self.args = {}
        
    def create_input_group(self):
        """Create the input file/folder selection group"""
        group = QGroupBox("Input Selection")
        layout = QVBoxLayout()
        
        # Input files/folder row
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Input:"))
        
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Select video file or folder...")
        input_layout.addWidget(self.input_edit)
        
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self.browse_input)
        input_layout.addWidget(browse_button)
        
        layout.addLayout(input_layout)
        
        # Show/hide additional options based on input type
        self.input_edit.textChanged.connect(self.on_input_changed)
        
        group.setLayout(layout)
        return group
    
    def create_options_group(self):
        """Create the main processing options group"""
        group = QGroupBox("Processing Options")
        layout = QGridLayout()
        
        # Row 1: Fisheye Width and Frame Rate
        layout.addWidget(QLabel("Fisheye Width:"), 0, 0)
        self.fisheye_width_spin = QSpinBox()
        self.fisheye_width_spin.setRange(0, 10000)
        self.fisheye_width_spin.setToolTip("Output fisheye image diameter in pixels (default: uses frame height)")
        layout.addWidget(self.fisheye_width_spin, 0, 1)
        
        layout.addWidget(QLabel("Frame Rate (Hz):"), 0, 2)
        self.frame_rate_spin = QDoubleSpinBox()
        self.frame_rate_spin.setRange(0.1, 30.0)
        self.frame_rate_spin.setSingleStep(0.1)
        self.frame_rate_spin.setDecimals(1)
        layout.addWidget(self.frame_rate_spin, 0, 3)
        
        # Row 2: Max Seconds and Frame Range
        layout.addWidget(QLabel("Max Seconds:"), 1, 0)
        self.max_seconds_spin = QDoubleSpinBox()
        self.max_seconds_spin.setRange(0.0, 3600.0)
        self.max_seconds_spin.setSingleStep(1.0)
        layout.addWidget(self.max_seconds_spin, 1, 1)
        
        layout.addWidget(QLabel("Start Frame:"), 1, 2)
        self.startf_spin = QSpinBox()
        self.startf_spin.setRange(1, 1000000)
        layout.addWidget(self.startf_spin, 1, 3)
        
        layout.addWidget(QLabel("End Frame:"), 1, 4)
        self.endf_spin = QSpinBox()
        self.endf_spin.setRange(1, 1000000)
        layout.addWidget(self.endf_spin, 1, 5)
        
        # Row 3: Sharpness Detection
        layout.addWidget(QLabel("Sharpness Detection:"), 2, 0)
        self.detect_sharpness_check = QCheckBox()
        self.detect_sharpness_check.setToolTip("Analyze video for sharpness and select best frames")
        layout.addWidget(self.detect_sharpness_check, 2, 1)
        
        layout.addWidget(QLabel("Crop Size:"), 2, 2)
        self.crop_size_combo = QComboBox()
        self.crop_size_combo.addItems(['64', '128', '256', '384', '512'])
        layout.addWidget(self.crop_size_combo, 2, 3)
        
        layout.addWidget(QLabel("Threshold (0-100):"), 2, 4)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 100.0)
        self.threshold_spin.setSingleStep(0.1)
        self.threshold_spin.setDecimals(1)
        layout.addWidget(self.threshold_spin, 2, 5)
        
        # Row 4: Output Mode
        layout.addWidget(QLabel("Output Mode:"), 3, 0)
        self.output_mode_layout = QHBoxLayout()
        
        self.fisheyeonly_check = QCheckBox("Fisheye Only")
        self.fisheyeonly_check.setToolTip("Generate only fisheye images")
        self.output_mode_layout.addWidget(self.fisheyeonly_check)
        
        self.e360only_check = QCheckBox("360° Only")
        self.e360only_check.setToolTip("Generate only 360° equirectangular images")
        self.output_mode_layout.addWidget(self.e360only_check)
        
        layout.addLayout(self.output_mode_layout, 3, 1, 1, 5)
        
        group.setLayout(layout)
        return group
    
    def create_advanced_group(self):
        """Create the advanced options group (conditional based on config)"""
        group = QGroupBox("Advanced Options")
        layout = QGridLayout()
        
        # Config status label
        self.config_status_label = QLabel(f"Config Available: {'Yes' if self.config_available else 'No'}")
        self.config_status_label.setStyleSheet("color: green;" if self.config_available else "color: red;")
        layout.addWidget(self.config_status_label, 0, 0, 1, 4)
        
        # Only show advanced options if config is NOT available
        if not self.config_available:
            row = 1
            
            # FFmpeg Path
            layout.addWidget(QLabel("FFmpeg Path:"), row, 0)
            self.ffmpeg_path_edit = QLineEdit()
            self.ffmpeg_path_edit.setPlaceholderText("Path to ffmpeg executable...")
            layout.addWidget(self.ffmpeg_path_edit, row, 1)
            
            ffmpeg_browse = QPushButton("Browse...")
            ffmpeg_browse.clicked.connect(self.browse_ffmpeg)
            layout.addWidget(ffmpeg_browse, row, 2)
            
            # Quality
            layout.addWidget(QLabel("Quality (2-6):"), row, 3)
            self.quality_spin = QSpinBox()
            self.quality_spin.setRange(1, 6)
            self.quality_spin.setValue(1)
            layout.addWidget(self.quality_spin, row, 4)
            
            row += 1
            
            # Debug
            self.debug_check = QCheckBox("Debug Mode")
            layout.addWidget(self.debug_check, row, 0, 1, 2)
            
            # LUT File
            layout.addWidget(QLabel("LUT File:"), row, 2)
            self.lut_file_edit = QLineEdit()
            self.lut_file_edit.setPlaceholderText("Path to LUT .npz file...")
            layout.addWidget(self.lut_file_edit, row, 3)
            
            lut_browse = QPushButton("Browse...")
            lut_browse.clicked.connect(self.browse_lut)
            layout.addWidget(lut_browse, row, 4)
        
        group.setLayout(layout)
        return group
    
    def create_progress_group(self):
        """Create the progress and log display group"""
        group = QGroupBox("Progress & Log")
        layout = QVBoxLayout()
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        layout.addWidget(self.progress_bar)
        
        # Log display
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        layout.addWidget(self.log_text)
        
        group.setLayout(layout)
        return group
    
    def browse_input(self):
        """Open file dialog to select input"""
        # Determine if we're looking for a file or folder
        # Based on the original logic, it can be either
        
        file_dialog = QFileDialog(self)
        file_dialog.setWindowTitle("Select Input")
        
        # Try both file and directory selection
        input_path, _ = file_dialog.getOpenFileName(
            self, "Select Video File", "",
            "Video Files (*.mp4 *.avi *.mov *.mkv *.ts);;All Files (*)"
        )
        
        if not input_path:
            # If no file selected, try directory
            input_path = file_dialog.getExistingDirectory(
                self, "Select Folder", ""
            )
        
        if input_path:
            self.input_edit.setText(input_path)
    
    def browse_ffmpeg(self):
        """Open file dialog to select ffmpeg executable"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select FFmpeg Executable", "",
            "Executables (*.exe);;All Files (*)"
        )
        
        if file_path:
            self.ffmpeg_path_edit.setText(file_path)
    
    def browse_lut(self):
        """Open file dialog to select LUT file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select LUT File (.npz)", "",
            "LUT Files (*.npz);;All Files (*)"
        )
        
        if file_path:
            self.lut_file_edit.setText(file_path)
    
    def on_input_changed(self, text):
        """Handle input changes to show/hide options"""
        # This could be expanded based on input type
        pass
    
    def start_processing(self):
        """Start the processing thread"""
        # Collect all arguments from GUI
        self.collect_args()
        
        # Validate arguments
        if not self.validate_args():
            return
        
        # Update UI for processing state
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setVisible(True)
        
        # Start processing thread
        self.processing_thread = ProcessingThread(self.args)
        self.processing_thread.progress_signal.connect(self.update_log)
        self.processing_thread.finished_signal.connect(self.processing_finished)
        self.processing_thread.start()
    
    def collect_args(self):
        """Collect all GUI arguments into the args dict"""
        self.args = {}
        
        # Input
        input_text = self.input_edit.text().strip()
        if input_text:
            # Split by spaces if multiple inputs
            self.args['input'] = input_text.split()
        else:
            self.args['input'] = []
        
        # Basic options
        self.args['fisheye_width'] = self.fisheye_width_spin.value() if self.fisheye_width_spin.value() != 0 else None
        self.args['frame_rate'] = self.frame_rate_spin.value() if self.frame_rate_spin.value() != 0.1 else None
        self.args['max_seconds'] = self.max_seconds_spin.value() if self.max_seconds_spin.value() != 0.0 else None
        self.args['startf'] = self.startf_spin.value() if self.startf_spin.value() != 1 else None
        self.args['endf'] = self.endf_spin.value() if self.endf_spin.value() != 1 else None
        
        # Boolean options
        self.args['detect_sharpness'] = self.detect_sharpness_check.isChecked()
        self.args['fisheye_only'] = self.fisheyeonly_check.isChecked()
        self.args['e360_only'] = self.e360only_check.isChecked()
        
        # Sharpness options
        self.args['crop_size'] = int(self.crop_size_combo.currentText())
        self.args['threshold'] = self.threshold_spin.value() if self.threshold_spin.value() != 0.0 else None
        
        # Advanced options (only if config available)
        if not self.config_available:
            self.args['ffmpeg_path'] = self.ffmpeg_path_edit.text().strip() or None
            self.args['quality'] = self.quality_spin.value() if self.quality_spin.value() != 1 else None
            self.args['debug'] = self.debug_check.isChecked()
            self.args['lut_file'] = self.lut_file_edit.text().strip() or None
        
    def validate_args(self):
        """Validate collected arguments"""
        errors = []
        
        # Check required input
        if not self.args.get('input'):
            errors.append("Input is required")
        
        # Log validation errors
        if errors:
            self.log_text.append("Validation Errors:")
            for error in errors:
                self.log_text.append(f"  ERROR: {error}")
                self.log_text.append("")
            return False
        
        return True
    
    def update_log(self, message):
        """Update the log display"""
        self.log_text.append(message)
        self.log_text.append("")
        # Auto-scroll to bottom
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
    
    def cancel_processing(self):
        """Cancel the processing"""
        if self.processing_thread and self.processing_thread.isRunning():
            self.processing_thread.is_cancelled = True
            self.update_log("Cancelling processing...")
    
    def processing_finished(self, return_code):
        """Handle completion of processing"""
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setVisible(False)
        
        if return_code == 0:
            QMessageBox.information(self, "Success", "Processing completed successfully!")
        else:
            QMessageBox.critical(self, "Error", f"Processing failed with error code {return_code}")
    
    def closeEvent(self, event):
        """Handle window close event"""
        if self.processing_thread and self.processing_thread.isRunning():
            self.cancel_processing()
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GoPro Frame Maker GUI")
    app.setApplicationVersion("1.0")
    
    # Set style
    app.setStyle('Fusion')
    
    window = GoProFrameMakerGUI()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()