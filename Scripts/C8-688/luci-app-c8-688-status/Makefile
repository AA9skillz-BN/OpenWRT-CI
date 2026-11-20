# SPDX-License-Identifier: MIT
include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-c8-688-status
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKG_MAINTAINER:=AA9skillz-BN

include $(INCLUDE_DIR)/package.mk

define Package/$(PKG_NAME)
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=NRadio C8-688 device status
  DEPENDS:=+luci-base +rpcd-mod-ucode +ucode-mod-fs +ucode-mod-ubus
  PKGARCH:=all
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
endef

define Build/Compile
endef

define Package/$(PKG_NAME)/install
	$(INSTALL_DIR) $(1)/www/luci-static/resources/view/c8-688-status
	$(INSTALL_DATA) ./htdocs/luci-static/resources/view/c8-688-status/overview.js $(1)/www/luci-static/resources/view/c8-688-status/overview.js
	$(INSTALL_DIR) $(1)/usr/share/luci/menu.d $(1)/usr/share/rpcd/acl.d $(1)/usr/share/rpcd/ucode
	$(INSTALL_DATA) ./root/usr/share/luci/menu.d/luci-app-c8-688-status.json $(1)/usr/share/luci/menu.d/luci-app-c8-688-status.json
	$(INSTALL_DATA) ./root/usr/share/rpcd/acl.d/luci-app-c8-688-status.json $(1)/usr/share/rpcd/acl.d/luci-app-c8-688-status.json
	$(INSTALL_DATA) ./root/usr/share/rpcd/ucode/c8-688-status.uc $(1)/usr/share/rpcd/ucode/c8-688-status.uc
endef

$(eval $(call BuildPackage,$(PKG_NAME)))
