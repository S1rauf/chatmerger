// /app/modules/webapp/static/forwarding.js

import { apiCall } from './api.js';
import { escapeHtml } from './ui.js';

const tg = window.Telegram.WebApp;

// --- Глобальные переменные для хранения кэша ---
let loadedRules = [];
let avitoAccountsCache = [];

/**
 * Основная функция загрузки: получает и правила, и аккаунты
 */
export async function loadForwardingRules() {
    const listDiv = document.getElementById('forwardingRulesList');
    listDiv.innerHTML = `<p class="status-loading">Загрузка правил...</p>`;

    // Загружаем одновременно и правила, и аккаунты для эффективности
    const [rules, accounts] = await Promise.all([
        apiCall('/api/forwarding-rules'),
        apiCall('/api/avito-accounts')
    ]);

    // Сохраняем в кэш
    loadedRules = rules || [];
    avitoAccountsCache = accounts || [];

    if (!rules) {
        listDiv.innerHTML = `<p class="status-error">Не удалось загрузить правила.</p>`;
        return;
    }

    if (rules.length === 0) {
        listDiv.innerHTML = '<p>Вы еще не создали ни одного приглашения.</p>';
        return;
    }

    listDiv.innerHTML = '';
    rules.forEach(rule => {
        const div = document.createElement('div');
        div.className = 'item-card';
        
        let statusHtml;
        let actionsHtml;

        if (rule.target_user_accepted) { 
            statusHtml = `<span class="status-ok">Принято (${escapeHtml(rule.target_tg_user_display_name)})</span>`;
            actionsHtml = `
                <button class="js-manage-permissions" data-id="${rule.id}">Права доступа</button>
                <button class="danger js-delete-forwarding" data-id="${rule.id}">Удалить</button>
            `;
        } else {
            statusHtml = `<span class="status-warning">Ожидает принятия</span>`;
            actionsHtml = `
                <button class="js-copy-invite" data-link="${escapeHtml(rule.invite_link)}">Копировать ссылку</button>
                <button class="danger js-delete-forwarding" data-id="${rule.id}">Удалить</button>
            `;
        }

        div.innerHTML = `
            <h4>${escapeHtml(rule.custom_rule_name)}</h4>
            <p><strong>Статус:</strong> ${statusHtml}</p>
            <div class="item-actions">${actionsHtml}</div>
        `;
        listDiv.appendChild(div);
    });
}

/**
 * Создает новое приглашение
 */
export async function createInvite() {
    const nameInput = document.getElementById('forwardingRuleName');
    const passwordInput = document.getElementById('forwardingInvitePassword');
    const canReplyCheckbox = document.getElementById('forwardingCanReply');
    
    const name = nameInput.value.trim();
    if (!name) {
        tg.showAlert('Пожалуйста, укажите имя или роль для этого приглашения (например, "Менеджер Василий").');
        return;
    }

    const payload = {
        custom_rule_name: name,
        invite_password: passwordInput.value.trim() || null,
        can_reply: canReplyCheckbox.checked
    };
    
    const result = await apiCall('/api/forwarding-rules', 'POST', payload);
    
    // Проверяем, что бэкенд ответил успехом
    if (result && result.success) {
        tg.HapticFeedback.notificationOccurred('success');
        
        // Показываем простое и понятное уведомление
        tg.showAlert(
            "Приглашение успешно создано! Найдите его в списке ниже и нажмите 'Копировать ссылку', чтобы поделиться."
        );
        
        // Очищаем форму, закрываем аккордеон и, самое главное, ОБНОВЛЯЕМ СПИСОК
        nameInput.value = '';
        passwordInput.value = '';
        canReplyCheckbox.checked = false;
        const accordionHeader = document.querySelector('#forwardingTab .accordion-header');
        if (accordionHeader.classList.contains('active')) {
            accordionHeader.click();
        }
        loadForwardingRules(); // <- Это действие немедленно покажет новое приглашение
    }
}

export function copyInviteLink(link) {
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(link)
            .then(() => tg.showAlert('Ссылка скопирована в буфер обмена!'))
            .catch(() => tg.showAlert('Не удалось скопировать ссылку.'));
    } else {
        tg.showAlert('Функция копирования недоступна. Пожалуйста, скопируйте ссылку вручную.');
    }
}

/**
 * Открывает модальное окно для редактирования прав
 * @param {string} ruleId - UUID правила
 */
export function openPermissionsModal(ruleId) {
    const rule = loadedRules.find(r => r.id === ruleId);
    if (!rule) return;

    document.getElementById('editingRuleId').value = ruleId;
    document.getElementById('permissionsModalTitle').textContent = `Права для: ${escapeHtml(rule.custom_rule_name)}`;
    
    const permissions = rule.permissions || {};
    document.getElementById('permissionCanReply').checked = permissions.can_reply || false;

    const accountsListDiv = document.getElementById('permissionAccountsList');
    accountsListDiv.innerHTML = 'Загрузка аккаунтов...';
    
    const allowedAccounts = permissions.allowed_accounts || [];

    if (avitoAccountsCache.length > 0) {
        accountsListDiv.innerHTML = '';
        avitoAccountsCache.forEach(acc => {
            // Если allowed_accounts - null, значит выбраны все.
            const isChecked = allowedAccounts === null || allowedAccounts.includes(acc.id);
            const accName = acc.custom_alias || `Профиль ${acc.avito_user_id}`;
            
            accountsListDiv.innerHTML += `
                <div class="checkbox-container">
                    <input type="checkbox" id="acc-perm-${acc.id}" value="${acc.id}" ${isChecked ? 'checked' : ''}>
                    <label for="acc-perm-${acc.id}">${escapeHtml(accName)}</label>
                </div>
            `;
        });
    } else {
        accountsListDiv.innerHTML = '<p>Нет Avito-аккаунтов для выбора.</p>';
    }
    
    document.getElementById('permissionsModal').classList.remove('hidden');
}

/**
 * Закрывает модальное окно
 */
export function closePermissionsModal() {
    document.getElementById('permissionsModal').classList.add('hidden');
}

/**
 * Сохраняет измененные права доступа
 */
export async function savePermissions() {
    const ruleId = document.getElementById('editingRuleId').value;
    if (!ruleId) return;

    const canReply = document.getElementById('permissionCanReply').checked;
    
    const selectedAccounts = Array.from(document.querySelectorAll('#permissionAccountsList input:checked')).map(cb => parseInt(cb.value));

    // Если выбраны все, отправляем null. Иначе - массив ID.
    const allowedAccounts = (selectedAccounts.length === avitoAccountsCache.length) ? null : selectedAccounts;
    
    const payload = {
        can_reply: canReply,
        allowed_accounts: allowedAccounts
    };
    
    const result = await apiCall(`/api/forwarding-rules/${ruleId}/permissions`, 'PUT', payload);
    if (result && result.success) {
        tg.HapticFeedback.notificationOccurred('success');
        closePermissionsModal();
        loadForwardingRules();
    }
}

/**
 * Удаляет правило пересылки
 * @param {string} ruleId 
 */
export function deleteForwardingRule(ruleId) {
    tg.showConfirm('Вы уверены, что хотите отозвать это приглашение / удалить помощника?', async (confirmed) => {
        if (confirmed) {
            const result = await apiCall(`/api/forwarding-rules/${ruleId}`, 'DELETE');
            if (result && result.success) {
                tg.HapticFeedback.notificationOccurred('success');
                loadForwardingRules();
            }
        }
    });
}
