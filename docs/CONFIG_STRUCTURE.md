# Habitat System Configuration Structure

This document describes the configuration file structure used throughout the Habitat system for modules and controllers.

## Overview

The Habitat system uses JSON configuration files to define settings for different components. Each module type has its own configuration structure while sharing common base settings.

## Common Configuration Sections

### Module-Level Configuration

All modules inherit these common configuration sections:

```json
{
  "module": {
    "heartbeat_interval": 30,
    "samplerate": 200,
    "status_reporting": true,
    "error_retry_count": 3,
    "error_retry_delay": 5
  },
  "service": {
    "port": 5353,
    "service_type": "_module._tcp.local.",
    "announce": true
  },
  "communication": {
    "command_socket_port": 5555,
    "status_socket_port": 5556,
    "data_format": "json",
    "compression": false
  },
  "health_monitor": {
    "cpu_check_enabled": true,
    "memory_check_enabled": true,
    "disk_check_enabled": true,
    "warning_threshold_cpu": 80,
    "warning_threshold_memory": 80,
    "warning_threshold_disk": 80
  },
  "file_transfer": {
    "max_retries": 3,
    "chunk_size": 65536,
    "timeout": 30
  },
  "logging": {
    "level": "INFO",
    "format": "%(asctime)s - %(levelname)s - %(message)s",
    "to_file": false,
    "max_file_size_mb": 10,
    "backup_count": 3
  },
  "recording_folder": "rec",
  "recording_filetype": "txt",
  "auto_export": true
}
```

### Readiness Validation Configuration

Modules can specify readiness validation parameters:

```json
{
  "module": {
    "required_disk_space_mb": 100.0,
    "ptp_offset_threshold_us": 1000.0
  }
}
```

## Module-Specific Configurations

### Camera Module Configuration

```json
{
  "camera": {
    "width": 1280,
    "height": 720,
    "fps": 30,
    "codec": "h264",
    "profile": "high",
    "level": 4.2,
    "bitrate": 10000000,
    "file_format": "mp4"
  },
  "recording": {
    "segment_length_seconds": 600,
    "pre_record_seconds": 5,
    "post_record_seconds": 5,
    "timestamp_overlay": true,
    "timestamp_format": "%Y-%m-%d %H:%M:%S",
    "timestamp_position": "bottom-right"
  },
  "streaming": {
    "enabled": true,
    "port": 8554,
    "transport": "udp"
  },
  "module": {
    "required_disk_space_mb": 1024.0,  // 1GB for video recording
    "ptp_offset_threshold_us": 500.0    // Tighter sync for video
  }
}
```

### Arduino Module Configuration

```json
{
  "editable": {
    "arduino": {
      "baudrate": 115200,
      "motor_controller": {
        "max_motor_speed": 400,
        "min_motor_speed": -400,
        "manual_motor_speed_for_2rpm": 95,
        "motor_speed_delay": 2,
        "encoder_pin": "A2"
      },
      "shock_controller": {
        "max_current": 5.1,
        "current_step": 0.2,
        "weak_shock": {
          "name": "weak_shock",
          "current": 0.2,
          "duration": 0.5,
          "intershock_latency": 1,
          "entrance_latency": 1.5,
          "outside_latency": 0.1
        }
      }
    }
  },
  "module": {
    "required_disk_space_mb": 50.0,    // 50MB for data logs
    "ptp_offset_threshold_us": 1000.0  // Standard sync
  }
}
```

### TTL Module Configuration

```json
{
  "ttl": {
    "output_pins": [1, 2, 3],
    "input_pins": [4, 5],
    "pulse_duration_ms": 100,
    "sampling_rate_hz": 1000
  },
  "module": {
    "required_disk_space_mb": 100.0,
    "ptp_offset_threshold_us": 1000.0
  }
}
```

### RFID Module Configuration

```json
{
  "rfid": {
    "reader_type": "usb",
    "baudrate": 9600,
    "timeout_ms": 1000,
    "antenna_power": 27
  },
  "module": {
    "required_disk_space_mb": 100.0,
    "ptp_offset_threshold_us": 1000.0
  }
}
```

### Microphone Module Configuration

```json
{
  "microphone": {
    "sample_rate": 44100,
    "channels": 1,
    "bit_depth": 16,
    "gain_db": 0
  },
  "module": {
    "required_disk_space_mb": 500.0,   // 500MB for audio recording
    "ptp_offset_threshold_us": 1000.0
  }
}
```

## Controller Configuration

```json
{
  "controller": {
    "max_buffer_size": 1000,
    "manual_control": true,
    "print_received_data": false,
    "zmq_commands": ["get_status", "get_data", "start_stream", "stop_stream", "record_video"],
    "cli_commands": ["get_status", "get_data", "start_stream", "stop_stream", "record_video"]
  },
  "service": {
    "port": 5000,
    "service_type": "_controller._tcp.local.",
    "service_name": "controller._controller._tcp.local."
  },
  "health_monitor": {
    "heartbeat_interval": 30,
    "heartbeat_timeout": 90
  },
  "experiment": {
    "rat_identity": {
      "name": "rat1",
      "sex": "male",
      "age": 3,
      "weight": 1.65,
      "notes": "notes"
    },
    "table_rotation_speed_rpm": 2,
    "max_shocks_per_experiment": 50,
    "trial_name": null
  }
}
```

## APA-Specific Configurations

### APA Camera Configuration

```json
{
  "editable": {
    "mask": {
      "mask_radius": 0.55,
      "mask_enabled": true,
      "mask_center_x_offset": 0,
      "mask_center_y_offset": 0
    },
    "shock_zone": {
      "shock_zone_display": true,
      "shock_zone_enabled": true,
      "shock_zone_angle_span_deg": 90,
      "shock_zone_start_angle_deg": 45,
      "shock_zone_inner_offset": 0.5,
      "shock_zone_color": [0, 255, 0],
      "shock_zone_thickness": 10
    },
    "camera": {
      "fps": 30,
      "width": 1332,
      "height": 990,
      "codec": "h264",
      "profile": "high",
      "level": 4.2,
      "intra": 30,
      "file_format": "h264"
    }
  },
  "module": {
    "required_disk_space_mb": 1024.0,
    "ptp_offset_threshold_us": 500.0
  }
}
```

## Configuration File Locations

- **Base configs:** `src/habitat/src/modules/config/`
- **APA configs:** `src/modules/`
- **Controller config:** `src/controller/config.json`

## Readiness Validation Parameters

The following parameters can be configured for readiness validation:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `module.required_disk_space_mb` | 100.0 | Minimum disk space required in MB |
| `module.ptp_offset_threshold_us` | 1000.0 | Maximum acceptable PTP offset in microseconds |

## Best Practices

1. **Module-specific settings:** Place module-specific settings under the module type key (e.g., `camera`, `arduino`)
2. **Readiness validation:** Configure disk space and PTP thresholds based on module requirements
3. **Editable sections:** Use the `editable` section for user-configurable parameters
4. **Sensible defaults:** Always provide reasonable default values
5. **Documentation:** Document any new configuration parameters

## Configuration Inheritance

Modules inherit from the base `generic_config.json` and can override specific sections. The inheritance order is:

1. Base generic config
2. Module-specific base config (e.g., `camera_config.json`)
3. Instance-specific config (e.g., `apa_camera_config.json`) 