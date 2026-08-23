// Lucide icon rendering. Templates declare icons with <i data-lucide="icon-name">;
// this replaces them with inline SVGs at runtime.
import {
    ArrowRightLeft,
    Bookmark,
    Calendar,
    CalendarClock,
    CalendarDays,
    CalendarX,
    Check,
    ChevronDown,
    ChevronLeft,
    ChevronRight,
    ChevronsLeft,
    ChevronsRight,
    CircleAlert,
    CircleCheck,
    CircleMinus,
    CirclePlay,
    CircleQuestionMark,
    Clapperboard,
    Clock,
    Copy,
    Database,
    DoorOpen,
    Ellipsis,
    ExternalLink,
    Eye,
    EyeOff,
    FastForward,
    FileInput,
    FileUp,
    Film,
    Flag,
    GalleryHorizontalEnd,
    House,
    Image,
    Info,
    Link,
    ListOrdered,
    Menu,
    Moon,
    Pause,
    Play,
    RadioTower,
    RefreshCw,
    RotateCcwClock,
    Search,
    Settings,
    Share,
    ShieldHalf,
    SkipForward,
    SlidersHorizontal,
    SquarePen,
    Star,
    Sun,
    Trash2,
    TriangleAlert,
    Tv,
    Unlink,
    User,
    Users,
    WandSparkles,
    Wrench,
    X,
} from 'lucide';
import { createIcons } from 'lucide';

const icons = {
    ArrowRightLeft,
    Bookmark,
    Calendar,
    CalendarClock,
    CalendarDays,
    CalendarX,
    Check,
    ChevronDown,
    ChevronLeft,
    ChevronRight,
    ChevronsLeft,
    ChevronsRight,
    CircleAlert,
    CircleCheck,
    CircleMinus,
    CirclePlay,
    CircleQuestionMark,
    Clapperboard,
    Clock,
    Copy,
    Database,
    DoorOpen,
    Ellipsis,
    ExternalLink,
    Eye,
    EyeOff,
    FastForward,
    FileInput,
    FileUp,
    Film,
    Flag,
    GalleryHorizontalEnd,
    House,
    Image,
    Info,
    Link,
    ListOrdered,
    Menu,
    Moon,
    Pause,
    Play,
    RadioTower,
    RefreshCw,
    RotateCcwClock,
    Search,
    Settings,
    Share,
    ShieldHalf,
    SkipForward,
    SlidersHorizontal,
    SquarePen,
    Star,
    Sun,
    Trash2,
    TriangleAlert,
    Tv,
    Unlink,
    User,
    Users,
    WandSparkles,
    Wrench,
    X,
};

export function renderIcons(root = document) {
    createIcons({ icons, root });
}

renderIcons();

// HTMX swaps inject new placeholders. afterSwap fires synchronously in the same
// task as the DOM insertion, so replacing them here happens before the browser
// paints the new content - no flash of unrendered <i> placeholders. afterSettle
// runs on a timer (settleDelay) and would let a frame slip through.
function renderSwappedIcons(event) {
    const root = event.detail?.target || event.target;

    if (root instanceof Element && root.matches('[data-lucide]')) {
        // outerHTML swap of a placeholder itself: querySelectorAll skips the root.
        renderIcons(root.parentNode || document);
    } else if (root instanceof Element || root instanceof DocumentFragment) {
        renderIcons(root);
    } else {
        renderIcons();
    }
}

document.addEventListener('htmx:afterSwap', renderSwappedIcons);
document.addEventListener('htmx:oobAfterSwap', renderSwappedIcons);
