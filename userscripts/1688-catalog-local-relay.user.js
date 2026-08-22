// ==UserScript==
// @name         1688 catalog local relay collector v3
// @namespace    local.1688.catalog
// @version      3.0.1
// @description  Capture one atomic product snapshot, queue it in IndexedDB, then sync to the localhost SQLite relay.
// @match        https://detail.1688.com/offer/*.html*
// @match        https://m.1688.com/offer/*.html*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';
    if (window.__catalogRelayV3Loaded) return;
    window.__catalogRelayV3Loaded = true;

    const RELAY_BASE = 'http://127.0.0.1:8765';
    const DEFAULT_TOKEN = 'CHANGE_ME_LOCAL_TOKEN';
    const DB_NAME = 'catalog-relay-outbox-v3';
    const STORE_NAME = 'records';
    const AUTO_COLLECT_DELAY_MS = 4500;
    const RETRY_INTERVAL_MS = 15000;
    let panel;
    let lastMessage = '准备中';

    function getToken() {
        return GM_getValue('relay_token', DEFAULT_TOKEN);
    }

    function setToken() {
        const token = prompt('请输入与 config.yaml relay.token 相同的本机中转令牌：', getToken());
        if (token && token.trim()) {
            GM_setValue('relay_token', token.trim());
            showToast('令牌已保存，只存于 Tampermonkey', 'ok');
            syncAll();
        }
    }

    function openOutbox() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, 1);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    const store = db.createObjectStore(STORE_NAME, { keyPath: 'offer_id' });
                    store.createIndex('queued_at', 'queued_at');
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    async function outboxPut(record) {
        const db = await openOutbox();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, 'readwrite');
            tx.objectStore(STORE_NAME).put({ ...record, queued_at: new Date().toISOString() });
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
    }

    async function outboxDelete(offerId) {
        const db = await openOutbox();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, 'readwrite');
            tx.objectStore(STORE_NAME).delete(offerId);
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
    }

    async function outboxAll() {
        const db = await openOutbox();
        return new Promise((resolve, reject) => {
            const request = db.transaction(STORE_NAME, 'readonly').objectStore(STORE_NAME).getAll();
            request.onsuccess = () => resolve(request.result || []);
            request.onerror = () => reject(request.error);
        });
    }

    function relayRequest(method, path, data) {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method,
                url: RELAY_BASE + path,
                headers: {
                    'Content-Type': 'application/json',
                    'X-Relay-Token': getToken(),
                },
                data: data ? JSON.stringify(data) : undefined,
                timeout: 12000,
                onload: response => {
                    let body = {};
                    try { body = JSON.parse(response.responseText || '{}'); } catch (_) {}
                    if (response.status >= 200 && response.status < 300) resolve(body);
                    else reject(new Error(`HTTP ${response.status}: ${body.detail || response.responseText || 'relay error'}`));
                },
                ontimeout: () => reject(new Error('本机中转连接超时')),
                onerror: () => reject(new Error('本机中转未启动或连接失败')),
            });
        });
    }

    function cleanText(value, limit = 3000) {
        return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
    }

    function extractOfferId() {
        const match = location.href.match(/offer[\/=](\d+)/i);
        return match ? match[1] : '';
    }

    function extractTitle() {
        const selectors = ['h1', '[class*="offer-title"]', '[class*="title-text"]', '[class*="title"]'];
        for (const selector of selectors) {
            const element = document.querySelector(selector);
            const value = cleanText(element?.innerText, 1000);
            if (value && value.length >= 3) return value;
        }
        return cleanText(document.title.replace(/[|｜].*$/, ''), 1000);
    }

    function extractCategory(title, specs) {
        const categories = [
            ['首饰套装', ['首饰套装', 'jewelry set', 'jewellery set', '饰品套装', '四件套', '三件套', '两件套']],
            ['项链', ['项链', '吊坠链', '颈链', 'choker', 'necklace']],
            ['手链', ['手链', '手串', 'bracelet']],
            ['手镯', ['手镯', '玉镯', 'bangle']],
            ['脚链', ['脚链', '脚镯', 'anklet', '脚饰']],
            ['耳环', ['耳环', '耳钉', '耳坠', 'earring']],
            ['戒指', ['戒指', '指环', ' ring ']],
            ['胸针', ['胸针', 'brooch', '胸花']],
            ['发饰', ['发饰', '发夹', '发簪', '发钗', '头饰', '发圈', '发绳']],
            ['串珠配件', ['串珠', '散珠', 'diy 配件', '水晶珠', '亚克力珠']],
            ['包装展示', ['首饰盒', '展示盒', '包装盒', '绒布袋', '展示架']],
            ['饰品配饰', ['饰品', '配饰', 'jewelry', 'jewellery', '首饰']],
        ];
        const breadcrumb = cleanText(Array.from(document.querySelectorAll('[class*="breadcrumb"], [class*="crumb"]')).map(e => e.innerText).join(' '), 1500);
        const text = ` ${title} ${breadcrumb} ${JSON.stringify(specs)} `.toLowerCase();
        for (const [category, keywords] of categories) {
            if (keywords.some(keyword => text.includes(keyword.toLowerCase()))) return category;
        }
        return '未分类';
    }

    function extractSpecs() {
        const specs = {};
        const selectors = [
            'table tr',
            '[class*="attribute"] li', '[class*="attribute"] div',
            '[class*="attributes"] li', '[class*="attributes"] div',
            '[class*="property"] li', '[class*="property"] div',
            '[class*="props"] li', '[class*="props"] div',
            '[class*="od-pc-attribute"] div',
        ];
        const seenElements = new Set();
        for (const selector of selectors) {
            for (const element of document.querySelectorAll(selector)) {
                if (seenElements.has(element)) continue;
                seenElements.add(element);
                const text = cleanText(element.innerText, 2200);
                if (!text || text.length > 2200) continue;
                const match = text.match(/^([^:：\n]{1,40})[:：\s]+(.{1,1000})$/);
                if (!match) continue;
                const key = cleanText(match[1], 80);
                const value = cleanText(match[2], 1500);
                if (key && value && !specs[key]) specs[key] = value;
                if (Object.keys(specs).length >= 80) return specs;
            }
        }
        return specs;
    }

    function extractPrice() {
        const visible = cleanText(document.body?.innerText, 100000);
        const tiers = [];
        const tierRe = /(\d+)\s*(?:[-~–—到至]\s*(\d+)?)?\s*(?:件|个|套|pcs?)\s*[^\d¥￥]{0,30}[¥￥]\s*(\d+(?:\.\d+)?)/gi;
        let match;
        while ((match = tierRe.exec(visible)) !== null) {
            tiers.push({ min_qty: Number(match[1]), max_qty: match[2] ? Number(match[2]) : null, unit_price: Number(match[3]) });
            if (tiers.length >= 20) break;
        }
        const unique = [];
        const seen = new Set();
        for (const tier of tiers.sort((a, b) => a.min_qty - b.min_qty)) {
            const key = `${tier.min_qty}|${tier.max_qty}|${tier.unit_price}`;
            if (!seen.has(key)) { seen.add(key); unique.push(tier); }
        }
        if (unique.length) {
            const prices = unique.map(t => t.unit_price);
            const low = Math.min(...prices), high = Math.max(...prices);
            return {
                raw: unique.map(t => `${t.min_qty}${t.max_qty ? '-' + t.max_qty : '+'}件 ¥${t.unit_price}`).join(' | '),
                display: low === high ? `¥${low.toFixed(2)}` : `¥${low.toFixed(2)}-${high.toFixed(2)} (阶梯价)`,
                tiers: unique,
            };
        }
        const priceElements = Array.from(document.querySelectorAll('[class*="price"], [class*="Price"]'))
            .map(e => cleanText(e.innerText, 500)).filter(Boolean).join(' | ');
        const direct = (priceElements || visible).match(/[¥￥]\s*(\d+(?:\.\d+)?)(?:\s*[-~–—到至]\s*[¥￥]?\s*(\d+(?:\.\d+)?))?/);
        if (direct) {
            const values = [Number(direct[1]), direct[2] ? Number(direct[2]) : Number(direct[1])];
            const low = Math.min(...values), high = Math.max(...values);
            return { raw: cleanText(direct[0], 500), display: low === high ? `¥${low.toFixed(2)}` : `¥${low.toFixed(2)}-${high.toFixed(2)}`, tiers: [] };
        }
        return { raw: visible.includes('询价') ? '询价' : '', display: visible.includes('询价') ? '询价' : '', tiers: [] };
    }

    function extractSize(specs) {
        const sizeEntries = [];
        for (const [key, value] of Object.entries(specs)) {
            if (/(尺寸|规格|长度|宽度|高度|直径|外径|内径|链长|珠径|戒圈|size|length|width|height|diameter)/i.test(key)) {
                sizeEntries.push(`${key}: ${value}`);
            }
        }
        if (sizeEntries.length) return { raw: sizeEntries.slice(0, 8).join('\n'), source: 'attribute' };
        const lines = String(document.body?.innerText || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        const matched = lines.filter(line =>
            /(?:尺寸|规格|长度|直径|链长|珠径|size)[：:]/i.test(line) ||
            /\d+(?:\.\d+)?\s*[×xX*]\s*\d+(?:\.\d+)?(?:\s*[×xX*]\s*\d+(?:\.\d+)?)?\s*(?:mm|cm|inch|in|英寸)/i.test(line)
        );
        return { raw: matched.slice(0, 8).map(v => cleanText(v, 500)).join('\n'), source: matched.length ? 'visible_text' : '' };
    }

    function elementImageUrls(element) {
        const urls = [];
        const attrs = ['src', 'data-src', 'data-lazy-src', 'data-lazyload-src', 'data-original', 'data-image'];
        if (element.currentSrc) urls.push(element.currentSrc);
        for (const attr of attrs) if (element.getAttribute?.(attr)) urls.push(element.getAttribute(attr));
        const srcset = element.getAttribute?.('srcset');
        if (srcset) urls.push(...srcset.split(',').map(part => part.trim().split(/\s+/)[0]));
        const poster = element.getAttribute?.('poster');
        if (poster) urls.push(poster);
        const style = getComputedStyle(element).backgroundImage || '';
        const bg = style.match(/url\(["']?([^"')]+)["']?\)/);
        if (bg) urls.push(bg[1]);
        return urls;
    }

    function normalizeImageUrl(raw) {
        if (!raw || /^data:/i.test(raw)) return '';
        try {
            const url = new URL(String(raw).replace(/\\\//g, '/').replace(/&amp;/g, '&'), location.href);
            if (!/^https?:$/.test(url.protocol)) return '';
            url.protocol = 'https:';
            return url.href;
        } catch (_) { return ''; }
    }

    function imageKey(url) {
        try {
            const parsed = new URL(url);
            const path = parsed.pathname
                .replace(/(\.(?:jpe?g|png|webp))_(?:\d+x\d+(?:q\d+)?|[^/?]*)\.(?:jpe?g|png|webp)$/i, '$1')
                .replace(/_\d+x\d+(?:q\d+)?(?=\.(?:jpg|jpeg|png|webp)$)/i, '')
                .toLowerCase();
            return (parsed.hostname + path).toLowerCase();
        } catch (_) { return url.toLowerCase(); }
    }

    function embeddedImageSize(url) {
        try {
            const path = decodeURIComponent(new URL(url).pathname);
            const tps = path.match(/-\d+-tps-(\d+)-(\d+)(?:\.|$)/i);
            if (tps) return [Number(tps[1]), Number(tps[2])];
            const resized = path.match(/_(\d+)x(\d+)(?:q\d+)?(?=\.(?:jpe?g|png|webp)$)/i);
            if (resized) return [Number(resized[1]), Number(resized[2])];
        } catch (_) { /* ignore malformed URLs */ }
        return null;
    }

    function isPageAssetUrl(url) {
        const lower = url.toLowerCase();
        if (!/\.(?:jpe?g|png|webp)(?:$|[?#_])/i.test(lower)) return true;
        if (/(?:avatar|logo|icon|loading|spacer|qrcode|emoji|sprite|favicon)/i.test(lower)) return true;
        if (/(?:-rate\.|\/rate\/|comment|review)/i.test(lower)) return true;
        if (/-\d+-tps-/i.test(lower)) return true;
        const size = embeddedImageSize(url);
        return Boolean(size && (size[0] < 320 || size[1] < 320));
    }

    function imageScore(url, sourcePriority) {
        let score = sourcePriority;
        try {
            const parsed = new URL(url);
            const host = parsed.hostname.toLowerCase();
            const path = parsed.pathname.toLowerCase();
            if (host === 'cbu01.alicdn.com' && path.includes('/img/ibank/')) score += 2400;
            else if (path.includes('/img/ibank/')) score += 1900;
            if (/-0-cib\./i.test(path)) score += 500;
            if (/\.(?:jpe?g|webp)(?:$|_)/i.test(path)) score += 120;
            if (path.includes('/imgextra/')) score -= 100;
        } catch (_) { /* normalized URLs are expected to parse */ }
        return score;
    }

    function extractImageUrls() {
        const highPrioritySelectors = [
            '[class*="gallery"] img', '[class*="Gallery"] img',
            '[class*="detail-gallery"] img', '[class*="image-viewer"] img',
            '[class*="main-image"] img', '[class*="mainImage"] img',
            '[class*="image-list"] img', '[class*="imageList"] img',
            '[class*="offer-img"] img', '[class*="od-pc-offer-image"] img',
            '[class*="thumbnail"] img', '[class*="thumb"] img',
            '[data-testid*="gallery"] img', '[data-testid*="image"] img',
            'video[poster]',
        ];
        const ordered = [];
        const seenElements = new Set();
        for (const selector of highPrioritySelectors) {
            for (const element of document.querySelectorAll(selector)) {
                if (!seenElements.has(element)) { seenElements.add(element); ordered.push([element, 700]); }
            }
        }
        for (const element of document.querySelectorAll('img')) {
            const rect = element.getBoundingClientRect();
            const source = `${element.currentSrc || ''} ${element.src || ''}`.toLowerCase();
            if ((rect.top + scrollY < 2200 && rect.width >= 45 && rect.height >= 45) || source.includes('imgextra')) {
                if (!seenElements.has(element)) { seenElements.add(element); ordered.push([element, 200]); }
            }
        }
        const ranked = new Map();
        let order = 0;
        function add(raw, sourcePriority = 0) {
            const url = normalizeImageUrl(raw);
            if (!url || isPageAssetUrl(url)) return;
            if (!/(alicdn|1688)/i.test(url)) return;
            const key = imageKey(url);
            const candidate = { url, score: imageScore(url, sourcePriority), order: order++ };
            const existing = ranked.get(key);
            if (!existing || candidate.score > existing.score) ranked.set(key, candidate);
        }
        ordered.forEach(([element, priority]) => elementImageUrls(element).forEach(url => add(url, priority)));

        const html = document.documentElement.outerHTML;
        const regex = /https?:\\?\/\\?\/[^"'\s<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"'\s<>]*)?/gi;
        let match;
        let scanned = 0;
        while ((match = regex.exec(html)) !== null && scanned < 512) {
            add(match[0], 0);
            scanned += 1;
        }
        return [...ranked.values()]
            .sort((left, right) => right.score - left.score || left.order - right.order)
            .slice(0, 32)
            .map(item => item.url);
    }

    function collectRecord() {
        const offerId = extractOfferId();
        if (!offerId) throw new Error('无法从当前网址识别 offer_id');
        const specs = extractSpecs();
        const title = extractTitle();
        return {
            schema_version: 3,
            offer_id: offerId,
            url: location.href,
            title,
            category: extractCategory(title, specs),
            price: extractPrice(),
            size: extractSize(specs),
            specs,
            image_urls: extractImageUrls(),
            collected_at: new Date().toISOString(),
        };
    }

    async function collectAndSync() {
        try {
            lastMessage = '正在读取当前商品'; refreshPanel();
            const record = collectRecord();
            await outboxPut(record);
            lastMessage = `已缓存 ${record.offer_id}，候选图 ${record.image_urls.length} 张`;
            refreshPanel();
            await syncOne(record);
        } catch (error) {
            lastMessage = `采集失败：${error.message}`;
            showToast(lastMessage, 'warn'); refreshPanel();
        }
    }

    async function syncOne(record) {
        try {
            if (getToken() === DEFAULT_TOKEN) throw new Error('请先设置随机 Relay Token');
            const response = await relayRequest('POST', '/api/v1/products', record);
            if (!response.ok || response.offer_id !== record.offer_id) throw new Error('中转返回的 offer_id 不一致');
            await outboxDelete(record.offer_id);
            lastMessage = `已入库 ${record.offer_id}，后台选图中`;
            showToast(lastMessage, 'ok');
            await refreshPanel();
            return true;
        } catch (error) {
            lastMessage = `已保留待发送：${error.message}`;
            refreshPanel();
            return false;
        }
    }

    async function syncAll() {
        const records = await outboxAll();
        for (const record of records) await syncOne(record);
        await refreshPanel();
    }

    async function exportBackup() {
        const records = await outboxAll();
        const blob = new Blob([JSON.stringify(records, null, 2)], { type: 'application/json;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `1688_unsynced_${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
    }

    async function refreshPanel() {
        if (!panel) return;
        const pending = (await outboxAll()).length;
        panel.querySelector('.relay-pending').textContent = String(pending);
        panel.querySelector('.relay-message').textContent = lastMessage;
        try {
            const stats = await relayRequest('GET', '/api/v1/stats');
            panel.querySelector('.relay-total').textContent = String(stats.total || 0);
            panel.querySelector('.relay-ready').textContent = String(stats.ready || 0);
            panel.querySelector('.relay-state').textContent = '本机服务在线';
            panel.querySelector('.relay-state').style.color = '#389e0d';
        } catch (_) {
            panel.querySelector('.relay-state').textContent = '本机服务离线';
            panel.querySelector('.relay-state').style.color = '#cf1322';
        }
    }

    function createPanel() {
        panel = document.createElement('div');
        panel.innerHTML = `
          <div style="font-weight:700;color:#ff6a00;margin-bottom:5px">1688 本机采集器 v3.0.1</div>
          <div class="relay-state">检查中</div>
          <div>数据库：<b class="relay-total">0</b> / 选图完成：<b class="relay-ready">0</b></div>
          <div>待发送：<b class="relay-pending">0</b></div>
          <div class="relay-message" style="font-size:11px;color:#666;min-height:32px;margin:5px 0">准备中</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">
            <button class="relay-collect">重新采集</button>
            <button class="relay-sync">同步待办</button>
            <button class="relay-token">设置令牌</button>
            <button class="relay-backup">备份待办</button>
          </div>`;
        panel.style.cssText = 'position:fixed;top:60px;right:12px;z-index:2147483647;width:235px;background:#fff;border:2px solid #ff6a00;border-radius:8px;padding:12px;font:13px/1.5 system-ui;box-shadow:0 4px 18px rgba(0,0,0,.2)';
        for (const button of panel.querySelectorAll('button')) button.style.cssText = 'border:0;border-radius:4px;padding:6px;cursor:pointer;background:#f0f0f0';
        panel.querySelector('.relay-collect').onclick = collectAndSync;
        panel.querySelector('.relay-sync').onclick = syncAll;
        panel.querySelector('.relay-token').onclick = setToken;
        panel.querySelector('.relay-backup').onclick = exportBackup;
        document.body.appendChild(panel);
    }

    function showToast(message, type) {
        const toast = document.createElement('div');
        toast.textContent = message;
        toast.style.cssText = `position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:2147483647;padding:10px 18px;border-radius:5px;color:#fff;background:${type === 'warn' ? '#cf1322' : '#389e0d'};font-weight:600`;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    async function start() {
        if (!document.body) return setTimeout(start, 300);
        createPanel();
        await refreshPanel();
        window.scrollTo({ top: Math.min(document.body.scrollHeight, 2400), behavior: 'smooth' });
        setTimeout(() => window.scrollTo({ top: 0, behavior: 'smooth' }), 1600);
        setTimeout(collectAndSync, AUTO_COLLECT_DELAY_MS);
        setInterval(syncAll, RETRY_INTERVAL_MS);
    }

    start();
})();
