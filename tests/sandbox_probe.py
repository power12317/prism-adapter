"""Fail if Chromium's actual namespace or seccomp sandbox is disabled."""
import os
from playwright.sync_api import sync_playwright

assert os.geteuid() == 10001
with sync_playwright() as p:
    with p.chromium.launch(executable_path=os.environ['PRISM_ADAPTER_CHROME'],
                           headless=True, chromium_sandbox=True) as browser:
        page = browser.new_page()
        page.goto('chrome://sandbox')
        text = page.locator('body').inner_text()
        print(text)
        assert 'Layer 1 Sandbox\tNamespace' in text, text
        for name in ('PID namespaces', 'Network namespaces', 'Seccomp-BPF sandbox'):
            assert f'{name}\tYes' in text, (name, text)
        assert 'You are adequately sandboxed.' in text
