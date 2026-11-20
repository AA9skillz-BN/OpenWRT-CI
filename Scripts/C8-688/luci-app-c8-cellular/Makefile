# SPDX-License-Identifier: MIT
include $(TOPDIR)/rules.mk
PKG_NAME:=luci-app-c8-cellular
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
include $(INCLUDE_DIR)/package.mk
define Package/$(PKG_NAME)
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=C8 cellular management with authenticated MT5700 WEBUI
  DEPENDS:=+luci-base +luci-app-mt5700m +c8-cellular-webui +python3-light +python3-asyncio +python3-urllib +python3-codecs +python3-logging +python3-openssl +flock +curl +ca-bundle
  PKGARCH:=all
endef
define Package/$(PKG_NAME)/conffiles
/etc/config/c8_cellular
/etc/mt5700m/slot-rollback
/etc/mt5700m/profile-rollback
endef
define Build/Compile
endef
define Package/$(PKG_NAME)/install
	$(INSTALL_DIR) $(1)
	$(CP) ./root/. $(1)/
	$(INSTALL_DIR) $(1)/www/5700 $(1)/www/luci-static/resources/view/c8-cellular
	$(INSTALL_DATA) ./htdocs/5700/c8-bridge.js $(1)/www/5700/c8-bridge.js
	$(INSTALL_DATA) ./htdocs/luci-static/resources/view/c8-cellular/overview.js $(1)/www/luci-static/resources/view/c8-cellular/overview.js
	chmod 0755 $(1)/usr/libexec/rpcd/luci.c8_cellular $(1)/usr/libexec/c8-cellular/action $(1)/etc/init.d/c8-cellular $(1)/etc/uci-defaults/94-c8-cellular
	chmod 0600 $(1)/etc/config/c8_cellular
endef
$(eval $(call BuildPackage,$(PKG_NAME)))
