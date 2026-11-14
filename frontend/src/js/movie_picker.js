// "Pick a movie" games (swipe, battle, dice). Runs entirely on the watchlist JSON
// the page embeds; nothing is persisted. Exposed as a global so Alpine finds it
// on first scan, so main.js must import this before htmx.js starts Alpine.

const DECK_SIZE = 12;
const SWIPE_OUT_MS = 220; // matches .picker-card transition
const CHOOSE_MS = 280; // matches .picker-duel transition

export function shuffle(items) {
    const out = [...items];
    for (let i = out.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
}

// Largest power of two that fits both the chosen bracket and the pool.
export function bracketSize(wanted, available) {
    let size = 1;
    while (size * 2 <= Math.min(wanted, available)) size *= 2;
    return size;
}

// Slot-machine frames: never the same poster twice in a row, ending on the
// winner. Built backwards from the winner so the last step can't repeat it.
// Needs at least two candidates.
export function revealFrames(candidates, winner, count = 12) {
    const frames = [winner];
    while (frames.length < count) {
        const options = candidates.filter((movie) => movie !== frames[0]);
        frames.unshift(options[Math.floor(Math.random() * options.length)]);
    }
    return frames;
}

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

window.moviePicker = (pool) => ({
    pool,
    mode: 'swipe',
    filters: { genres: [], people: [], maxRuntime: 0 },
    showInfo: false,
    deck: [],
    liked: [],
    swiping: false,
    drag: null,
    wantedBracket: 8,
    round: [],
    winners: [],
    matchIndex: 0,
    chosen: null,
    rolling: false,
    flash: null,
    winner: null,
    run: 0,

    init() {
        this.reset();
        this.$watch('filters', () => this.reset());
        this.$watch('mode', () => this.reset());
        this.$watch('wantedBracket', () => this.reset());
    },

    get genres() {
        return [...new Set(this.pool.flatMap((movie) => movie.genres))].sort((a, b) => a.localeCompare(b));
    },

    get filtered() {
        const { genres, people, maxRuntime } = this.filters;
        return this.pool.filter(
            (movie) =>
                (!genres.length || genres.some((genre) => movie.genres.includes(genre))) &&
                (!people.length || people.some((name) => movie.people.includes(name))) &&
                (!maxRuntime || (movie.runtime && movie.runtime <= maxRuntime)),
        );
    },

    get staged() {
        return this.winner || this.flash;
    },

    meta(movie, withGenres = false) {
        return [
            movie.score != null ? `★ ${movie.score}` : movie.ratings[0] && `${movie.ratings[0].label} ${movie.ratings[0].value}`,
            movie.year,
            movie.runtime && `${movie.runtime} min`,
            withGenres && movie.genres.join(', '),
        ]
            .filter(Boolean)
            .join(' · ');
    },

    toggleGenre(genre) {
        const { genres } = this.filters;
        this.filters.genres = genres.includes(genre) ? genres.filter((g) => g !== genre) : [...genres, genre];
    },

    reset() {
        this.run++;
        this.winner = this.flash = this.chosen = this.drag = null;
        this.rolling = this.swiping = false;
        this.deck = shuffle(this.filtered).slice(0, DECK_SIZE);
        this.liked = [];
        this.round = shuffle(this.filtered).slice(0, bracketSize(this.wantedBracket, this.filtered.length));
        this.winners = [];
        this.matchIndex = 0;
    },

    // ---- result reveal (shared) ----
    reveal(candidates) {
        const winner = candidates[Math.floor(Math.random() * candidates.length)];
        if (!winner) return;
        this.winner = null;
        if (candidates.length === 1 || reducedMotion()) {
            this.winner = winner;
            return;
        }
        const frames = revealFrames(candidates, winner);
        frames.forEach((movie) => movie.poster && (new Image().src = movie.poster));
        const run = ++this.run;
        this.rolling = true;
        let delay = 45;
        const step = () => {
            if (run !== this.run) return;
            this.flash = frames.shift();
            if (!frames.length) {
                this.rolling = false;
                this.winner = this.flash;
                return;
            }
            delay *= 1.15;
            setTimeout(step, delay);
        };
        step();
    },

    // ---- swipe ----
    topCard() {
        return this.$root.querySelector('[data-top-card]');
    },

    dragStart(event) {
        if (this.drag || this.swiping || event.button > 0) return;
        const el = event.currentTarget;
        el.setPointerCapture(event.pointerId);
        el.style.transition = 'none';
        this.drag = { el, id: event.pointerId, x: event.clientX, t: performance.now(), dx: 0 };
    },

    dragMove(event) {
        const drag = this.drag;
        if (!drag || event.pointerId !== drag.id) return;
        drag.dx = event.clientX - drag.x;
        drag.el.style.transform = `translateX(${drag.dx}px) rotate(${drag.dx / 18}deg)`;
        drag.el.querySelector('[data-stamp="like"]').style.opacity = Math.max(0, drag.dx / 90);
        drag.el.querySelector('[data-stamp="nope"]').style.opacity = Math.max(0, -drag.dx / 90);
    },

    dragEnd(event) {
        const drag = this.drag;
        if (!drag || event.pointerId !== drag.id) return;
        this.drag = null;
        const distance = Math.abs(drag.dx);
        const velocity = distance / (performance.now() - drag.t);
        drag.el.style.transition = '';
        if (distance < 6) this.showInfo = !this.showInfo; // a tap, not a drag
        if (distance > 110 || (distance > 20 && velocity > 0.11)) {
            this.swipe(drag.dx > 0);
            return;
        }
        drag.el.style.transform = '';
        drag.el.querySelectorAll('[data-stamp]').forEach((stamp) => (stamp.style.opacity = ''));
    },

    swipe(like) {
        const card = this.deck[0];
        const el = this.topCard();
        if (!card || !el || this.swiping || this.rolling) return;
        this.swiping = true;
        el.style.transition = '';
        el.style.transform = `translateX(${like ? 140 : -140}%) rotate(${like ? 18 : -18}deg)`;
        el.style.opacity = '0';
        el.querySelector(`[data-stamp="${like ? 'like' : 'nope'}"]`).style.opacity = '1';
        if (like) this.liked.push(card);
        const run = this.run;
        setTimeout(() => {
            if (run !== this.run) return;
            this.deck.shift();
            this.swiping = false;
            if (!this.deck.length && this.liked.length) this.reveal(this.liked);
        }, reducedMotion() ? 0 : SWIPE_OUT_MS);
    },

    onKey(event) {
        if (this.mode !== 'swipe' || event.target.closest('input, textarea, [contenteditable]')) return;
        if (event.key === 'ArrowRight') this.swipe(true);
        if (event.key === 'ArrowLeft') this.swipe(false);
        if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault();
            this.showInfo = !this.showInfo;
        }
    },

    // ---- battle ----
    get pair() {
        return this.round.slice(this.matchIndex * 2, this.matchIndex * 2 + 2);
    },

    choose(movie) {
        if (this.chosen || this.winner) return;
        this.chosen = movie.id;
        const run = this.run;
        setTimeout(() => {
            if (run !== this.run) return;
            this.chosen = null;
            this.winners.push(movie);
            this.matchIndex++;
            if (this.matchIndex * 2 < this.round.length) return;
            if (this.winners.length === 1) {
                this.winner = movie;
                return;
            }
            this.round = this.winners;
            this.winners = [];
            this.matchIndex = 0;
        }, reducedMotion() ? 0 : CHOOSE_MS);
    },
});
