import { bind, play } from 'cuelume';

bind();

const pendingWatchedRequests = new Set();

document.body.addEventListener('htmx:beforeRequest', function (event) {
    const elt = event.detail.elt;
    if (
        elt instanceof Element &&
        elt.matches('[data-watched-toggle]') &&
        event.detail.requestConfig?.verb === 'post'
    ) {
        pendingWatchedRequests.add(event.detail.xhr);
    }
});

document.body.addEventListener('htmx:afterRequest', function (event) {
    if (
        pendingWatchedRequests.delete(event.detail.xhr) &&
        event.detail.successful
    ) {
        play('success');
    }
});
