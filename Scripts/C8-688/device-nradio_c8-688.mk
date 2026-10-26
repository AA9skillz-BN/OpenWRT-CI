# ---------------------------------------------------------------------------
# NRadio / 鲲鹏无限 C8-688 —— 设备定义片段
# 由 Scripts/C8-688/private.sh 追加到 target/linux/mediatek/image/filogic.mk 末尾
# 结构参照 in-tree 同门机型 nradio_c8-668gl
#
# 实机分区（2026-10-26 实测）：kernel_2nd = 128 MiB、rootfs_2nd = 6.56 GiB，
# 空间富裕，所以不设 IMAGE_SIZE；overlay 由 fstools 在 rootfs 剩余空间建 loop+f2fs。
# ---------------------------------------------------------------------------
define Device/nradio_c8-688
  DEVICE_VENDOR := NRadio
  DEVICE_MODEL := C8-688
  DEVICE_DTS := mt7981b-nradio-c8-688
  DEVICE_DTS_DIR := ../dts
  DEVICE_PACKAGES := kmod-mt7915e kmod-mt7981-firmware mt7981-wo-firmware \
	kmod-phy-maxlinear kmod-hwmon-pwmfan \
	kmod-usb3 kmod-usb-net-cdc-ether kmod-usb-net-qmi-wwan \
	kmod-usb-serial-option kmod-usb-acm \
	f2fsck mkf2fs automount
  IMAGE/sysupgrade.bin := sysupgrade-tar | append-metadata
endef
TARGET_DEVICES += nradio_c8-688
