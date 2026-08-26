const copyLabel = (button, label) => {
    const text = button.querySelector('span');
    if (text) {
        text.textContent = label;
    }
};

const copyText = async (value) => {
    if (navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(value);
            return true;
        } catch {
            // Fall through to the legacy copy path when permission is denied.
        }
    }

    const helper = document.createElement('textarea');
    helper.value = value;
    helper.setAttribute('readonly', '');
    helper.style.position = 'fixed';
    helper.style.opacity = '0';
    document.body.appendChild(helper);
    helper.select();
    const copied = document.execCommand('copy');
    helper.remove();
    return copied;
};

document.addEventListener('click', (event) => {
    const copyButton = event.target.closest('[data-copy-target]');
    if (!copyButton) {
        return;
    }

    event.preventDefault();
    const input = document.querySelector(copyButton.dataset.copyTarget);
    if (!input) {
        return;
    }

    const defaultLabel = copyButton.dataset.defaultLabel || copyButton.querySelector('span')?.textContent || '';
    copyButton.dataset.defaultLabel = defaultLabel;
    copyText(input.value)
        .then((copied) => {
            if (!copied) {
                return;
            }
            copyLabel(copyButton, copyButton.dataset.copiedLabel || 'Copied');
            window.setTimeout(() => copyLabel(copyButton, defaultLabel), 1600);
        })
        .catch(() => {});
});
