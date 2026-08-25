import tippy from 'tippy.js';

// Turns `.media-rating` into a drag-to-pick control. On a mouse it behaves the
// way star ratings always have (hover previews, click commits); on touch it
// behaves like a range slider: press, slide across the stars to preview, lift
// to commit. A single tooltip follows whichever star is currently active so
// the value is readable even with a finger covering it.

const CONTAINER = '.media-rating';
const STAR = 'input[name="score"]';

let tooltip = null;
let anchor = null;
let drag = null;

function stars(container) {
    return Array.from(container.querySelectorAll(STAR));
}

function restore(container) {
    const score = container.dataset.score || '';
    stars(container).forEach((star) => {
        star.checked = star.value === score;
    });
}

function starAt(container, x, y) {
    const element = document.elementFromPoint(x, y);
    const star = element && element.closest ? element.closest(STAR) : null;
    return star && container.contains(star) ? star : null;
}

// Resolve a pointer position to a star even when it falls between or outside
// them, so a sloppy drag still tracks: past the last star clamps to 5, before
// the first star lands on "no rating".
function resolve(container, x, y) {
    const direct = starAt(container, x, y);
    if (direct) {
        return direct;
    }

    const all = stars(container);
    const rated = all.filter((star) => star.value !== '');
    if (!rated.length) {
        return null;
    }

    const first = rated[0].getBoundingClientRect();
    const last = rated[rated.length - 1].getBoundingClientRect();
    if (x < first.left) {
        return all.find((star) => star.value === '') || rated[0];
    }
    if (x > last.right) {
        return rated[rated.length - 1];
    }
    return (
        rated.find((star) => {
            const rect = star.getBoundingClientRect();
            return x >= rect.left && x <= rect.right;
        }) || null
    );
}

function ensureTooltip() {
    if (!tooltip) {
        tooltip = tippy(document.body, {
            theme: 'wygiwyh',
            placement: 'top',
            trigger: 'manual',
            hideOnClick: false,
            appendTo: () => document.body,
            zIndex: 1089,
            getReferenceClientRect: () =>
                anchor ? anchor.getBoundingClientRect() : new DOMRect(),
        });
    }
    return tooltip;
}

function showTooltip(star) {
    anchor = star;
    const instance = ensureTooltip();
    instance.setContent(star.getAttribute('aria-label') || star.value);
    instance.show();
    // The reference rect is read once per position pass, so nudge it after a
    // content change to keep the box centred on the new star.
    instance.popperInstance?.update();
}

function hideTooltip() {
    anchor = null;
    tooltip?.hide();
}

function preview(container, x, y) {
    const star = resolve(container, x, y);
    if (!star) {
        return null;
    }
    stars(container).forEach((candidate) => {
        candidate.checked = candidate === star;
    });
    showTooltip(star);
    return star;
}

function endDrag() {
    if (!drag) {
        return;
    }
    const {container, pointerId} = drag;
    drag = null;
    if (container.hasPointerCapture?.(pointerId)) {
        container.releasePointerCapture(pointerId);
    }
    hideTooltip();
}

document.addEventListener('pointerdown', (event) => {
    if (event.button !== undefined && event.button !== 0) {
        return;
    }
    const container = event.target.closest?.(CONTAINER);
    if (!container || !event.target.closest(STAR)) {
        return;
    }

    // Owning the gesture ourselves keeps the page from scrolling mid-drag and
    // stops the browser committing a rating the finger merely passed over.
    event.preventDefault();
    drag = {container, pointerId: event.pointerId};
    try {
        container.setPointerCapture(event.pointerId);
    } catch {
        drag.pointerId = null;
    }
    const star = preview(container, event.clientX, event.clientY);
    star?.focus?.({preventScroll: true});
});

document.addEventListener('pointermove', (event) => {
    if (drag) {
        event.preventDefault();
        preview(drag.container, event.clientX, event.clientY);
        return;
    }
    if (event.pointerType !== 'mouse') {
        return;
    }
    const container = event.target.closest?.(CONTAINER);
    if (container && event.target.closest(STAR)) {
        preview(container, event.clientX, event.clientY);
    }
});

document.addEventListener('pointerup', (event) => {
    if (!drag) {
        return;
    }
    const {container} = drag;
    endDrag();

    const star = resolve(container, event.clientX, event.clientY);
    if (!star) {
        restore(container);
        return;
    }
    if (star.value === (container.dataset.score || '')) {
        restore(container);
        return;
    }
    star.checked = true;
    star.dispatchEvent(new Event('change', {bubbles: true}));
});

document.addEventListener('pointercancel', () => {
    if (!drag) {
        return;
    }
    const {container} = drag;
    endDrag();
    restore(container);
});

// mouseleave does not bubble, so listen on the capture phase.
document.addEventListener(
    'mouseleave',
    (event) => {
        if (drag || !event.target.matches?.(CONTAINER)) {
            return;
        }
        hideTooltip();
        restore(event.target);
    },
    true,
);

// daisyUI plays its `rating` keyframe on every child of `.rating`, and a CSS
// animation replays whenever its element is inserted. This component swaps its
// own outerHTML on each rating POST, so all eleven inputs arrive as new
// elements and the pop fires across the whole row -- which with `rating-half`
// drags a dark seam down every star. The stylesheet disables it wholesale;
// replay it here on the one half-star that was actually picked, which is the
// selection feedback daisyUI gives when nothing is being re-inserted.

let pendingPulse = false;

function pulse(star) {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
        return;
    }
    star.style.animation = '0.25s ease-out rating';
    star.addEventListener(
        'animationend',
        () => {
            star.style.animation = '';
        },
        {once: true},
    );
}

// Covers both the change we dispatch on pointerup and a native keyboard one.
document.addEventListener('change', (event) => {
    const star = event.target.closest?.(STAR);
    if (star && star.closest(CONTAINER) && star.value) {
        pendingPulse = true;
    }
});

document.addEventListener('htmx:afterSwap', (event) => {
    if (!pendingPulse) {
        return;
    }
    const root = event.target;
    const container = root?.matches?.(CONTAINER)
        ? root
        : root?.querySelector?.(CONTAINER);
    if (!container) {
        return;
    }
    pendingPulse = false;
    const star = container.querySelector(`${STAR}:checked`);
    if (star && star.value) {
        pulse(star);
    }
});

// A rejected rating never swaps, so drop the intent rather than leaving it to
// fire on some later unrelated swap.
document.addEventListener('htmx:afterRequest', () => {
    pendingPulse = false;
});
