import 'hyperscript.org';
import './_htmx.js';
import Alpine from "alpinejs";
import mask from '@alpinejs/mask';
import collapse from '@alpinejs/collapse'
import { create, all } from 'mathjs';

window.Alpine = Alpine;
window.math = create(all, {
    number: 'BigNumber',
});

Alpine.plugin(mask);
Alpine.plugin(collapse);
Alpine.start();
