# Static assets from the locked inotdream project. No upstream daemon or CGI.
include $(TOPDIR)/rules.mk
PKG_NAME:=c8-cellular-webui
PKG_VERSION:=2.2.2
PKG_RELEASE:=1
include $(INCLUDE_DIR)/package.mk
define Package/c8-cellular-webui
  SECTION:=net
  CATEGORY:=Network
  TITLE:=C8 reviewed MT5700 WEBUI assets
  PKGARCH:=all
endef
define Build/Compile
endef
define Package/c8-cellular-webui/install
	$(INSTALL_DIR) $(1)/www/5700
	$(CP) ./files/www/5700/. $(1)/www/5700/
endef
$(eval $(call BuildPackage,c8-cellular-webui))
