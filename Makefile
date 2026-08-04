LAUNCHD_LABEL := mwm
LAUNCHD_PLIST := $(HOME)/Library/LaunchAgents/$(LAUNCHD_LABEL).plist
LAUNCHD_DOMAIN := gui/$(shell id -u)
LOCAL_BIN := $(HOME)/.local/bin
MWM_BIN := $(LOCAL_BIN)/mwm
PYINSTALLER_WORK := /tmp/mwm-pyinstaller-$(shell id -u)

.PHONY: all lint fix test clean install uninstall install_bin install_plist uninstall_bin uninstall_plist
.SILENT:

all: fix lint test

lint:
	uv run ruff --quiet --config pyproject.toml check .
	uv run basedpyright --project pyproject.toml --level error .

fix:
	uv run ruff --quiet --config pyproject.toml check --extend-select I --fix-only --fix .
	uv run ruff format --quiet .

test:
	uv run python -m doctest README.md $(wildcard *.py)

clean:
	rm -rf $(PYINSTALLER_WORK)
	rm -rf build dist
	rm -f *.spec

install: install_bin install_plist

install_plist:
	mkdir -p $(HOME)/Library/LaunchAgents
	$(MWM_BIN) launchd-plist > $(LAUNCHD_PLIST)
	-launchctl bootout $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	launchctl bootstrap $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	launchctl kickstart -k $(LAUNCHD_DOMAIN)/$(LAUNCHD_LABEL)

install_bin:
	mkdir -p $(LOCAL_BIN)
	mkdir -p $(PYINSTALLER_WORK)/build $(PYINSTALLER_WORK)/spec
	uv run --with pyinstaller --with-requirements mwm.py pyinstaller --noconfirm --onefile --name mwm --distpath $(LOCAL_BIN) --workpath $(PYINSTALLER_WORK)/build --specpath $(PYINSTALLER_WORK)/spec mwm.py

uninstall: uninstall_plist uninstall_bin

uninstall_plist:
	-launchctl bootout $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	rm -f $(LAUNCHD_PLIST)

uninstall_bin:
	rm -f $(LOCAL_BIN)/mwm
