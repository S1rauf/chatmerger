// static/main-tab.js
import { apiCall } from './api.js?v=1.0.0';
import { escapeHtml } from './ui.js?v=1.0.0';

const tg = window.Telegram.WebApp;
let loadedAccounts = [];

export async function loadMainStatus() {
    const card = document.getElementById('mainStatusCard');
    if (!card) {
        console.error("Element with id 'mainStatusCard' not found!");
        return;
    }

    const statusData = await apiCall('/api/main-status');
    if (!statusData) {
        card.innerHTML = `<p class="status-error">Не удалось загрузить статус.</p>`;
        card.classList.remove('status-loading');
        return;
    }

    const warningDiv = document.getElementById('termsAgreementWarning');
    if (warningDiv) {
        if (!statusData.has_agreed_to_terms) {
            warningDiv.innerHTML = `
                <p><strong>Важно!</strong> Для начала работы необходимо принять Пользовательское Соглашение.</p>
                <button id="showTermsBtn">Ознакомиться и принять</button>
            `;
            warningDiv.classList.remove('hidden');
        } else {
            warningDiv.classList.add('hidden');
        }
    }

    const tariffLine = statusData.tariff_expires_at_display
        ? `${escapeHtml(statusData.current_tariff_display)} (${escapeHtml(statusData.tariff_expires_at_display)})`
        : escapeHtml(statusData.current_tariff_display);

    card.innerHTML = `
        <p><strong>Тариф:</strong> ${tariffLine}</p>
        <p><strong>Баланс:</strong> ${escapeHtml(statusData.user_balance_rub_str)}</p>
        <button id="manageBillingBtn">Управление тарифами</button>
    `;
    card.classList.remove('status-loading');
    
    document.getElementById('manageBillingBtn').addEventListener('click', () => {
        // Находим кнопку вкладки по data-атрибуту и симулируем клик
        const tariffTabButton = document.querySelector('.tab-button[data-tab="tariffsTab"]');
        if (tariffTabButton) {
            tariffTabButton.click();
        }
    });

    const btnAdd = document.getElementById('btnAddAvitoAccount');
    btnAdd.classList.remove('hidden');
    btnAdd.onclick = () => {
        // Просим пользователя открыть ссылку, так как прямой переход может блокироваться
        tg.showConfirm('Перейти на сайт Avito для авторизации?', (confirmed) => {
            if (confirmed) {
                tg.openLink(statusData.auth_url);
            }
        });
    };
}

export async function loadAvitoAccounts() {
    const listDiv = document.getElementById('avitoAccountsList');
    listDiv.innerHTML = `<p class="status-loading">Загрузка аккаунтов...</p>`;
    const accounts = await apiCall('/api/avito-accounts');
    
    // =========================================================
    //
    //           !!! ДОБАВЛЯЕМ ОТЛАДКУ ЗДЕСЬ !!!
    //
    // Выводим в консоль то, что получили от сервера.
    //
    // =========================================================
    console.log("Полученные аккаунты с сервера:", accounts); 

    if (!accounts || !Array.isArray(accounts)) {
        listDiv.innerHTML = `<p class="status-error">Не удалось загрузить аккаунты.</p>`;
        console.error("Данные аккаунтов не являются массивом или пришли как null/undefined.");
        return;
    }
    loadedAccounts = accounts;
    
    if (accounts.length === 0) {
        listDiv.innerHTML = `<p>Нет подключенных аккаунтов.</p>`;
        return;
    }

    listDiv.innerHTML = '';
    accounts.forEach(acc => {
        // Добавим еще один лог, чтобы видеть данные каждого отдельного аккаунта
        console.log("Обработка аккаунта для отображения:", acc);

        const div = document.createElement('div');
        div.className = 'item-card avito-account-card';
        
        // Более надежная проверка на наличие chats_count
        const chatsCount = (typeof acc.chats_count === 'number') ? acc.chats_count : 'N/A';
        const tokenStatusText = acc.token_status_text || "Ошибка";
        const tokenStatusClass = acc.token_status_class || "status-error";

        div.innerHTML = `
            <div class="avito-account-info">
                <p><strong>${acc.is_active_tg_setting ? '➡️ ' : ''}${escapeHtml(acc.custom_alias || acc.avito_profile_name || `Профиль ${acc.avito_user_id}`)}</strong></p>
                <p class="hint">Статус: <span class="${acc.token_status_class}">${acc.token_status_text}</span> | Чатов: ${chatsCount}</p>
            </div>
            <div class="avito-account-actions">
                 <button class="small-btn js-sync-account" data-id="${acc.id}" title="Синхронизировать чаты">🔄</button>
                 <button class="small-btn js-manage-account" data-id="${acc.id}">Управлять</button>
            </div>
        `;
        listDiv.appendChild(div);
    });
}

export async function syncAccount(accountId) {
    const button = document.querySelector(`.js-sync-account[data-id="${accountId}"]`);
    if(button) button.innerHTML = '...'; // Показываем индикатор загрузки

    const result = await apiCall(`/api/avito-accounts/${accountId}/sync-chats`, 'POST');
    
    if (result && result.success) {
        tg.HapticFeedback.notificationOccurred('success');
        // Находим карточку и обновляем в ней количество чатов
        const card = button.closest('.avito-account-card');
        if (card) {
            const hint = card.querySelector('.hint');
            if (hint) {
                // Просто заменяем число в строке
                hint.textContent = hint.textContent.replace(/Чатов: \d+/, `Чатов: ${result.chats_count}`);
            }
        }
    }
    if(button) button.innerHTML = '🔄'; // Возвращаем иконку
}

export function openManageModal(accountId) {
    const account = loadedAccounts.find(acc => acc.id === accountId);
    if (!account) return;

    document.getElementById('managingAccountId').value = accountId;
    document.getElementById('manageAccountModalTitle').textContent = `Управление: ${escapeHtml(account.custom_alias || account.avito_profile_name || `ID ${account.avito_user_id}`)}`;
    document.getElementById('accountAliasInput').value = account.custom_alias || '';

    const modal = document.getElementById('manageAccountModal');
    if(modal) {
        modal.classList.remove('hidden');
    }
}

export function closeManageModal() {
    const modal = document.getElementById('manageAccountModal');
    if(modal) {
        modal.classList.add('hidden');
    }
}

export async function saveAlias() {
    const accountId = document.getElementById('managingAccountId').value;
    const alias = document.getElementById('accountAliasInput').value.trim();
    
    const result = await apiCall(`/api/avito-accounts/${accountId}/alias`, 'PUT', { alias: alias });
    if (result && result.success) {
        tg.HapticFeedback.notificationOccurred('success');
        closeManageModal();
        loadAvitoAccounts();
    }
}

export async function deleteAccount() {
    const accountId = document.getElementById('managingAccountId').value;
    const account = loadedAccounts.find(acc => acc.id === parseInt(accountId));
    
    tg.showConfirm(`Вы уверены, что хотите отключить аккаунт "${escapeHtml(account.custom_alias || account.avito_profile_name)}"?`, async (confirmed) => {
        if (confirmed) {
            const result = await apiCall(`/api/avito-accounts/${accountId}`, 'DELETE');
            if (result && result.success) {
                tg.HapticFeedback.notificationOccurred('success');
                tg.showAlert('Аккаунт успешно отключен.');
                closeManageModal();
                // Перезагружаем и статус, и аккаунты
                loadAvitoAccounts();
                loadMainStatus();
            }
        }
    });
}
// Новая функция для открытия модального окна
export function openTermsModal() {
    apiCall('/api/main-status').then(data => {
        if (data && data.terms_text) {
            const plainText = data.terms_text.replace(/<[^>]*>?/gm, '');
            document.getElementById('termsModalBody').textContent = plainText;
            document.getElementById('termsModal').classList.remove('hidden');
        }
    });
}

export function closeTermsModal() {
    document.getElementById('termsModal').classList.add('hidden');
}

export async function acceptTerms() {
    const result = await apiCall('/api/user/accept-terms', 'POST');
    if (result && result.success) {
        Telegram.WebApp.HapticFeedback.notificationOccurred('success');
        Telegram.WebApp.showAlert('Спасибо! Теперь вы можете полноценно пользоваться сервисом.');
        closeTermsModal();
        loadMainStatus(); // Перезагружаем статус, чтобы убрать предупреждение
    }
}