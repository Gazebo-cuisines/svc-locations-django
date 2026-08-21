This unit: **Zebra TC22F** · sticker **GUN-01** · serial `26202524703110` · Android 14 (SDK 34) · build `14-20-14.00-UG-U00-STD-ATH-04` · GMS · Snapdragon **QCM5430** · ~5.3 GB RAM · 1080×2160 @ 480dpi · BT name `BSD01_Gazeboo_cloud`

### IDs (this gun)

| Use | Value |
|---|---|
| Sticker / barcode | `GUN-01` (admin can PATCH to `GUN-03` etc.) |
| Hardware serial (PK from the gun) | `26202524703110` — USB iSerial, `ro.serialno`, `adb devices` |
| Zebra UUID | `f375db664e6b0501F9F8DCBd39d840a` |
| Factory BT MAC | `88:BC:AC:D5:21:BF` |
| ANDROID_ID | `d008cd109f0700c6` (resets — do not use as PK) |
| USB | `VID_05E0` `PID_2106` |

Wi‑Fi MAC is randomized (`e6:37:24:07:18:d7`). Battery serial `T0575` is hot-swap. No IMEI (Wi‑Fi SKU).

Warehouse app sends `X-Device-Serial` + optional `X-Device-Nickname`. Admin PCs send neither; APIs still work.

Print the sticker code (`GUN-03`) on the gun, not the 14-digit serial. Allocate with PATCH `assigned_user_id` on `/hardware/devices/GUN-03/`.

### Capture
- SE4710 2D imager (hardware trigger / SCAN key / `SYMBOL_TRIGGER_1–8`)
- DataWedge 13.0.325, EMDK 13.0.23, DataCapture Vision 2.1
- Rear 16 MP (OV16E10) + flash, AF, raw
- Front 5 MP (OV5675)
- ZCam 2.4.11
- NFC PN7160 (HCE / reader) + Enterprise NFC 2.1
- Dual mics, speaker (no 3.5 mm jack, no dedicated beeper)
- Vibrator, notification LED, charging LED

### Radios / connect
- Wi‑Fi (inc. Direct, Passpoint, RTT) — currently `GC-STAFF` (`172.16.0.98`)
- Bluetooth + BLE (HID host/device)
- USB-C: device, host, MTP, ADB
- Ethernet + workstation cradle
- MicroSD slot
- Google Play / ARCore

### Sensors
- Accel, gyro, magnetometer (ST LSM6DSO + MEMSIC)
- Ambient light, proximity
- Gravity / linear accel / rotation / orientation
- Step counter + detector, significant motion, tilt, pickup
- Stationary / motion detect
- Hall effect, free-fall
- No fingerprint, no ToF, no pressure

### Keys
- Side scan trigger, volume, power
- PTT, camera, shortcuts, lamp/keylight
- Glove / wet / stylus touch modes

### Print / deploy
- Android print + NetPrintService + ezlabel
- StageNow 13, LifeGuard OTA, MX/EMDK, license mgr, lock-task / kiosk, WorryFree Wi‑Fi
- Battery swap APIs (hot-swap on this family)

### Not on this SKU (TC22F)
- Cellular / eSIM / WWAN
- GPS
- Onboard UHF RFID (optional sled only; `sled_support=0` here)
- MSR, IrDA, USB wedge scanner, SimulScan, UART, GPIO cradle, smart holster

### Already on the device (user apps)
`com.gazeboerp.warehouse` 1.0.0 · Expo Go · ezlabel · NetPrintService
