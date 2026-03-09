#!/usr/bin/env python3
"""
MFRC522 RFID Reader for Raspberry Pi 5
Uses spidev for SPI + lgpio for RST pin (no RPi.GPIO dependency).
"""

import time
import logging

logger = logging.getLogger('rfid_reader')

# ── MFRC522 Registers ──
CommandReg     = 0x01
CommIEnReg     = 0x02
CommIrqReg     = 0x04
ErrorReg       = 0x06
FIFODataReg    = 0x09
FIFOLevelReg   = 0x0A
ControlReg     = 0x0C
BitFramingReg  = 0x0D
ModeReg        = 0x11
TxControlReg   = 0x14
TxASKReg       = 0x15
TModeReg       = 0x2A
TPrescalerReg  = 0x2B
TReloadRegH    = 0x2C
TReloadRegL    = 0x2D
VersionReg     = 0x37

# ── Commands ──
PCD_Idle       = 0x00
PCD_Transceive = 0x0C
PCD_ResetPhase = 0x0F

# ── PICC commands ──
PICC_REQIDL    = 0x26
PICC_ANTICOLL  = 0x93

MI_OK          = 0
MI_ERR         = 2


class MFRC522Reader:
    """Lightweight MFRC522 driver using spidev + lgpio for Pi 5."""

    def __init__(self, bus=0, device=0, rst_pin=25, gpio_chip=0):
        import spidev
        import lgpio

        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = 1000000
        self.spi.mode = 0

        self.rst_pin = rst_pin
        self._lgpio = lgpio
        self._h = lgpio.gpiochip_open(gpio_chip)
        lgpio.gpio_claim_output(self._h, self.rst_pin)

        self._reset()
        self._init()
        logger.info(f"MFRC522 ready (SPI {bus}:{device}, RST=GPIO{rst_pin}, "
                     f"ver=0x{self._read(VersionReg):02X})")

    # ── Low-level SPI ──
    def _write(self, addr, val):
        self.spi.xfer2([(addr << 1) & 0x7E, val])

    def _read(self, addr):
        return self.spi.xfer2([((addr << 1) & 0x7E) | 0x80, 0])[1]

    def _set_bits(self, reg, mask):
        self._write(reg, self._read(reg) | mask)

    def _clear_bits(self, reg, mask):
        self._write(reg, self._read(reg) & ~mask)

    # ── Init ──
    def _reset(self):
        self._lgpio.gpio_write(self._h, self.rst_pin, 1)
        time.sleep(0.05)
        self._write(CommandReg, PCD_ResetPhase)
        time.sleep(0.05)

    def _init(self):
        self._write(TModeReg, 0x8D)
        self._write(TPrescalerReg, 0x3E)
        self._write(TReloadRegL, 30)
        self._write(TReloadRegH, 0)
        self._write(TxASKReg, 0x40)
        self._write(ModeReg, 0x3D)
        # Turn antenna on
        tx = self._read(TxControlReg)
        if not (tx & 0x03):
            self._set_bits(TxControlReg, 0x03)

    # ── Card communication ──
    def _transceive(self, data):
        self._write(CommIEnReg, 0x77 | 0x80)
        self._clear_bits(CommIrqReg, 0x80)
        self._set_bits(FIFOLevelReg, 0x80)
        self._write(CommandReg, PCD_Idle)

        for byte in data:
            self._write(FIFODataReg, byte)

        self._write(CommandReg, PCD_Transceive)
        self._set_bits(BitFramingReg, 0x80)

        for _ in range(2000):
            n = self._read(CommIrqReg)
            if n & 0x01 or n & 0x30:
                break
        else:
            return MI_ERR, [], 0

        self._clear_bits(BitFramingReg, 0x80)

        if self._read(ErrorReg) & 0x1B:
            return MI_ERR, [], 0

        back_len = 0
        fifo_n = self._read(FIFOLevelReg)
        last_bits = self._read(ControlReg) & 0x07
        back_len = (fifo_n - 1) * 8 + last_bits if last_bits else fifo_n * 8

        fifo_n = min(fifo_n, 16) or 1
        back_data = [self._read(FIFODataReg) for _ in range(fifo_n)]

        return MI_OK, back_data, back_len

    # ── Public API ──
    def read_id_no_block(self):
        """Non-blocking: returns UID int or None if no card present."""
        # Request
        self._write(BitFramingReg, 0x07)
        status, _, back_bits = self._transceive([PICC_REQIDL])
        if status != MI_OK or back_bits != 0x10:
            return None

        # Anti-collision
        self._write(BitFramingReg, 0x00)
        status, uid_data, _ = self._transceive([PICC_ANTICOLL, 0x20])
        if status != MI_OK or len(uid_data) != 5:
            return None

        # Verify checksum
        check = 0
        for i in range(4):
            check ^= uid_data[i]
        if check != uid_data[4]:
            return None

        uid = 0
        for i in range(4):
            uid = (uid << 8) | uid_data[i]
        return uid

    def cleanup(self):
        try:
            self.spi.close()
        except Exception:
            pass
        try:
            self._lgpio.gpiochip_close(self._h)
        except Exception:
            pass
