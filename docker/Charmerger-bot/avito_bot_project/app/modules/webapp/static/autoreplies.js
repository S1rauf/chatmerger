import { apiCall } from './api.js';
import { escapeHtml } from './ui.js';

const tg = window.Telegram.WebApp;

// Глобальные переменные для хранения состояния вкладки
let loadedAutoReplies = [];
let selectedAccountId = null;

/**
 * Заполняет выпадающий список (select) доступными Avito-аккаунтами.
 */
export async function populateAutoreplyAccountSelector() {
    const accounts = await apiCall('/api/avito-accounts');
    console.log("1. populateAutoreplyAccountSelector started.");

    const select = document.getElementById('autoreplyAccountSelector');
    if (!select) {
        console.error("CRITICAL: Element with id 'autoreplyAccountSelector' NOT FOUND in HTML!");
        return;
    }
    
    console.log("2. Select element found. Disabling it and setting 'loading' text.");
    select.disabled = true;
    select.innerHTML = '<option>Загрузка аккаунтов...</option>';
    
    // --- САМЫЙ ВАЖНЫЙ ЛОГ ---
    console.log("3. Received data from /api/avito-accounts:", accounts);
    // Вы должны увидеть в консоли массив с вашим аккаунтом.
    // Например: [{ id: 1, custom_alias: null, ... }]

    if (accounts && Array.isArray(accounts) && accounts.length > 0) {
        console.log("4. Accounts data is valid and not empty. Clearing select and adding options.");
        select.innerHTML = '<option value="">-- Выберите аккаунт --</option>';
        accounts.forEach(acc => {
            const optionText = acc.custom_alias || `Профиль ${acc.avito_user_id}`;
            console.log(`   - Adding option: value=${acc.id}, text='${optionText}'`);
            const option = new Option(optionText, acc.id);
            select.add(option);
        });
        select.disabled = false;
        console.log("5. Finished adding options. Enabling select.");
    } else {
        console.log("4b. No accounts found or data is invalid. Setting 'no accounts' message.");
        select.innerHTML = '<option>Сначала добавьте Avito-аккаунт</option>';
    }
}

/**
 * Загружает и отображает правила автоответов для текущего selectedAccountId.
 */
export async function loadAutoReplies() {
    
    const listDiv = document.getElementById('autoRepliesList');
    const contentDiv = document.getElementById('autoRepliesContent');

    if (!selectedAccountId) {
        listDiv.innerHTML = '';
        contentDiv.classList.add('hidden'); // Скрываем блок с формой и списком
        return;
    }
    
    contentDiv.classList.remove('hidden'); // Показываем блок
    listDiv.innerHTML = `<p class="status-loading">Загрузка правил для аккаунта...</p>`;

    // Запрашиваем правила для конкретного аккаунта
    const rules = await apiCall(`/api/avito-accounts/${selectedAccountId}/autoreplies`);
    loadedAutoReplies = rules || [];
    
    listDiv.innerHTML = '';
    if (rules && Array.isArray(rules) && rules.length > 0) {
        rules.sort((a, b) => a.name.localeCompare(b.name)).forEach(r => {
            const keywords = r.keywords_list || [];
            const div = document.createElement('div');
            div.className = 'item-card';
            div.innerHTML = `
                <h4>
                    ${escapeHtml(r.name)} 
                    <span class="status-${r.is_active ? 'ok' : 'error'}">(${r.is_active ? 'Активно' : 'Выключено'})</span>
                </h4>
                <p><b>Ключевые слова:</b> ${keywords.length > 0 ? escapeHtml(keywords.join(', ')) : '<i>Любое сообщение</i>'}</p>
                <p class="autoreply-text-preview"><b>Ответ:</b> ${escapeHtml(r.reply_text.substring(0, 150))}${r.reply_text.length > 150 ? '...' : ''}</p>
                <div class="item-actions">
                    <button class="js-edit-autoreply" data-id="${r.id}">Изменить</button>
                    <button class="danger js-delete-autoreply" data-id="${r.id}">Удалить</button>
                </div>`;
            listDiv.appendChild(div);
        });
    } else if (rules && Array.isArray(rules)) {
        listDiv.innerHTML = '<p>Для этого аккаунта правила автоответов еще не созданы.</p>';
    } else {
        listDiv.innerHTML = '<p class="status-error">Не удалось загрузить правила.</p>';
    }
}

/**
 * Очищает форму создания/редактирования правила.
 */
export function clearAutoReplyForm() {
    document.getElementById('autoReplyId').value = '';
    document.getElementById('autoReplyName').value = '';
    document.getElementById('autoReplyKeywords').value = '';
    document.getElementById('autoReplyMatchType').value = 'contains_any';
    document.getElementById('autoReplyText').value = '';
    document.getElementById('autoReplyIsActive').checked = true;
    document.getElementById('autoReplyDelay').value = '0';
    document.querySelector('#autoRepliesTab .accordion-header').textContent = 'Добавить новое правило';
}

/**
 * Заполняет форму данными для редактирования правила.
 * @param {string} id - UUID правила для редактирования.
 */
export function editAutoReply(id) {
    const rule = loadedAutoReplies.find(r => r.id === id);
    if (!rule) return;
    
    document.getElementById('autoReplyId').value = rule.id;
    document.getElementById('autoReplyName').value = rule.name;
    document.getElementById('autoReplyKeywords').value = (rule.keywords_list || []).join(', ');
    document.getElementById('autoReplyMatchType').value = rule.match_type;
    document.getElementById('autoReplyText').value = rule.reply_text;
    document.getElementById('autoReplyIsActive').checked = rule.is_active;
    document.getElementById('autoReplyDelay').value = rule.delay_seconds || 0;

    const header = document.querySelector('#autoRepliesTab .accordion-header');
    header.textContent = 'Изменить правило';
    if (!header.classList.contains('active')) {
        header.click(); // Раскрываем аккордеон с формой
    }
    document.getElementById('autoReplyName').focus();
}

/**
 * Собирает данные из формы и отправляет на бэкенд для создания/обновления.
 */
export async function saveAutoReply() {
    console.log("1. saveAutoReply function started.");

    if (!selectedAccountId) {
        tg.showAlert('Пожалуйста, сначала выберите Avito-аккаунт.');
        console.error("Save stopped: no selectedAccountId.");
        return;
    }
    console.log(`2. selectedAccountId is: ${selectedAccountId}`);

    const id = document.getElementById('autoReplyId').value;
    const name = document.getElementById('autoReplyName').value.trim();
    const keywordsRaw = document.getElementById('autoReplyKeywords').value.trim();
    const matchType = document.getElementById('autoReplyMatchType').value;
    const text = document.getElementById('autoReplyText').value.trim();
    const isActive = document.getElementById('autoReplyIsActive').checked;
    const delay = parseInt(document.getElementById('autoReplyDelay').value, 10) || 0;
    // Считываем новое поле для кулдауна
    const cooldown = parseInt(document.getElementById('autoReplyCooldown')?.value, 10) || 3600;

    console.log(`3. Form data collected: name='${name}', text='${text}'`);

    if (!name || !text) {
        tg.showAlert('Название правила и текст ответа обязательны.');
        console.error("Save stopped: name or text is empty.");
        return;
    }

    const keywords = keywordsRaw.split(',').map(kw => kw.trim()).filter(Boolean);
    
    const payload = { 
        name, 
        trigger_keywords: keywords,
        reply_text: text, 
        match_type: matchType, 
        is_active: isActive,
        delay_seconds: delay,
        cooldown_seconds: cooldown // Добавляем кулдаун в payload
    };
    console.log("4. Payload prepared:", payload);

    const endpoint = id 
        ? `/api/autoreplies/${id}`
        : `/api/avito-accounts/${selectedAccountId}/autoreplies`;
    const method = id ? 'PUT' : 'POST';
    
    console.log(`5. Calling apiCall with method: ${method}, endpoint: ${endpoint}`);
    const result = await apiCall(endpoint, method, payload);

    console.log("6. apiCall finished. Result:", result);
    
    if (result && result.success) {
        console.log("7. Operation successful. Clearing form and reloading list.");
        tg.HapticFeedback.notificationOccurred('success');
        clearAutoReplyForm();
        const accordionHeader = document.querySelector('#autoRepliesTab .accordion-header');
        if (accordionHeader.classList.contains('active')) {
            accordionHeader.click();
        }
        loadAutoReplies();
    } else {
        console.error("8. Operation failed or result was falsy.", result);
    }
}

/**
 * Запрашивает подтверждение и удаляет правило.
 * @param {string} id - UUID правила для удаления.
 */
export function deleteAutoReply(id) {
    tg.showConfirm(`Вы уверены, что хотите удалить это правило?`, async (confirmed) => {
        if (confirmed) {
            const result = await apiCall(`/api/autoreplies/${id}`, 'DELETE');
            if (result && result.success) { // Проверяем успешный ответ
                tg.HapticFeedback.notificationOccurred('success');
                loadAutoReplies();
                clearAutoReplyForm();
            }
        }
    });
}


/**
 *  Этот блок вешает обработчик на выпадающий список.
 *  Он должен выполняться один раз при загрузке основного скрипта.
 */
document.addEventListener('DOMContentLoaded', () => {
    const selector = document.getElementById('autoreplyAccountSelector');
    if (selector) {
        selector.addEventListener('change', (e) => {
            // Сохраняем выбранный ID аккаунта в глобальную переменную
            selectedAccountId = e.target.value;
            // Загружаем правила для этого аккаунта
            loadAutoReplies();
        });
    }
});