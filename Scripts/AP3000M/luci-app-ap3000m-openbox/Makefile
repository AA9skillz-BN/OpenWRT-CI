# SPDX-License-Identifier: MIT
include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-ap3000m-openbox
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKG_MAINTAINER:=AA9skillz-BN

include $(INCLUDE_DIR)/package.mk

define Package/$(PKG_NAME)
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=AP3000M Open-Box official installer
  DEPENDS:=+luci-base +rpcd-mod-ucode +ucode-mod-fs +curl +ca-bundle +jq +flock +kmod-tun +kmod-nft-queue +kmod-nft-nat +kmod-veth +ip-full
  PKGARCH:=all
endef

define Package/$(PKG_NAME)/description
  Official Open-Box installation entry and sysupgrade data preservation.
  No proprietary Open-Box programs or assets are included in the firmware.
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
endef

define Build/Compile
endef

define Package/$(PKG_NAME)/install
	$(INSTALL_DIR) $(1)/usr/sbin $(1)/lib/upgrade/keep.d
	$(INSTALL_BIN) ./root/usr/sbin/ap3000m-openbox $(1)/usr/sbin/ap3000m-openbox
	$(INSTALL_DATA) ./root/lib/upgrade/keep.d/ap3000m-openbox $(1)/lib/upgrade/keep.d/ap3000m-openbox
	$(INSTALL_DIR) $(1)/www/luci-static/resources/view/ap3000m-openbox
	$(INSTALL_DATA) ./htdocs/luci-static/resources/view/ap3000m-openbox/overview.js $(1)/www/luci-static/resources/view/ap3000m-openbox/overview.js
	$(INSTALL_DIR) $(1)/usr/share/luci/menu.d $(1)/usr/share/rpcd/acl.d $(1)/usr/share/rpcd/ucode
	$(INSTALL_DATA) ./root/usr/share/luci/menu.d/luci-app-ap3000m-openbox.json $(1)/usr/share/luci/menu.d/luci-app-ap3000m-openbox.json
	$(INSTALL_DATA) ./root/usr/share/rpcd/acl.d/luci-app-ap3000m-openbox.json $(1)/usr/share/rpcd/acl.d/luci-app-ap3000m-openbox.json
	$(INSTALL_DATA) ./root/usr/share/rpcd/ucode/ap3000m-openbox.uc $(1)/usr/share/rpcd/ucode/ap3000m-openbox.uc
endef

$(eval $(call BuildPackage,$(PKG_NAME)))
