const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const scriptPath = path.join(__dirname, '..', 'userscripts', '1688-catalog-local-relay.user.js');
let source = fs.readFileSync(scriptPath, 'utf8');
const marker = /\r?\n    start\(\);\r?\n\}\)\(\);\s*$/;
assert(marker.test(source), 'userscript startup marker changed');
source = source.replace(
    marker,
    '\n    globalThis.__imageTestApi = { extractImageUrls, imageKey, isPageAssetUrl };\n})();\n',
);

function imageElement(attrs, rect = { top: 100, width: 500, height: 500 }) {
    return {
        currentSrc: attrs.currentSrc || '',
        src: attrs.src || '',
        getAttribute(name) { return attrs[name] || null; },
        getBoundingClientRect() { return rect; },
    };
}

const hero = 'https://cbu01.alicdn.com/img/ibank/O1CN-HERO!!123-0-cib.jpg_.webp';
const heroThumb = 'https://cbu01.alicdn.com/img/ibank/O1CN-HERO!!123-0-cib.jpg_300x300.jpg';
const hiddenOne = 'https://cbu01.alicdn.com/img/ibank/O1CN-HIDDEN-1!!123-0-cib.jpg_.webp';
const hiddenTwo = 'https://cbu01.alicdn.com/img/ibank/O1CN-HIDDEN-2!!123-0-cib.jpg_.webp';
const iconSvg = 'https://img.alicdn.com/imgextra/i2/ui-55-tps-24-24.svg';
const tinyTps = 'https://gw.alicdn.com/imgextra/i2/ui-2-tps-120-64.png';
const avatar = 'https://cbu01.alicdn.com/avatar/member-avatar.jpg';
const reviewImage = 'https://cbu01.alicdn.com/i1/O1CN-USER!!0-0-rate.jpg_b.jpg';

const galleryHero = imageElement({ currentSrc: hero, src: heroThumb });
const allImages = [
    galleryHero,
    imageElement({ src: iconSvg }, { top: 10, width: 24, height: 24 }),
    imageElement({ src: tinyTps }, { top: 20, width: 120, height: 64 }),
    imageElement({ src: avatar }),
    imageElement({ src: reviewImage }),
];

const context = {
    URL,
    Date,
    Map,
    Set,
    Number,
    String,
    RegExp,
    console,
    globalThis: null,
    window: {},
    location: { href: 'https://detail.1688.com/offer/123456.html' },
    scrollY: 0,
    getComputedStyle() { return { backgroundImage: '' }; },
    document: {
        body: null,
        documentElement: {
            outerHTML: `
                <script>
                  {"images":["${hiddenOne.replaceAll('/', '\\/')}","${hiddenTwo}"]}
                </script>
                <img src="${iconSvg}">
                <img src="${tinyTps}">
                <img src="${reviewImage}">
            `,
        },
        querySelectorAll(selector) {
            if (selector === 'img') return allImages;
            if (selector.includes('gallery')) return [galleryHero];
            return [];
        },
    },
    setTimeout() {},
    setInterval() {},
    GM_getValue() {},
    GM_setValue() {},
    GM_xmlhttpRequest() {},
};
context.globalThis = context;

vm.createContext(context);
vm.runInContext(source, context, { filename: scriptPath });

const { extractImageUrls, imageKey, isPageAssetUrl } = context.__imageTestApi;
const urls = Array.from(extractImageUrls());

assert.deepStrictEqual(urls.slice(0, 3), [
    hero,
    hiddenOne.replace(/_\.webp$/, ''),
    hiddenTwo.replace(/_\.webp$/, ''),
]);
assert.strictEqual(urls.filter(url => imageKey(url) === imageKey(hero)).length, 1, 'hero variants must deduplicate');
assert(!urls.some(url => url.includes('tps-')), 'TPS page assets must be removed');
assert(!urls.some(url => /avatar|rate\.jpg/i.test(url)), 'avatar/review images must be removed');
assert.strictEqual(isPageAssetUrl(iconSvg), true);
assert.strictEqual(isPageAssetUrl(tinyTps), true);
assert.strictEqual(isPageAssetUrl(hero), false);

// Regression fixture from a real failed record: two product images were mixed
// with many Alibaba UI assets. Only the /img/ibank/ product photos may survive.
const realProductOne = 'https://cbu01.alicdn.com/img/ibank/2989154489_1467425893.jpg_.webp';
const realProductTwo = 'https://cbu01.alicdn.com/img/ibank/10016649909_1467425893.jpg_.webp';
context.document.querySelectorAll = () => [];
context.document.documentElement.outerHTML = `
    <img src="https://img.alicdn.com/imgextra/i2/O1CN01vPS4dX1YdboQ4A7pk_!!6000000003082-55-tps-15-8.svg">
    <img src="https://gw.alicdn.com/imgextra/i4/O1CN01w3y4Hz216HrUb3bYL_!!6000000006935-2-tps-36-36.png">
    <img src="${realProductOne}">
    <img src="${realProductTwo}">
    <img src="https://cbu01.alicdn.com/i1/O1CN019N1BJ31vQ5IB3QxJU_!!0-0-rate.jpg_b.jpg">
`;
const realUrls = Array.from(extractImageUrls());
assert.deepStrictEqual(realUrls, [
    realProductOne.replace(/_\.webp$/, ''),
    realProductTwo.replace(/_\.webp$/, ''),
]);

console.log('userscript image filter: ok');
