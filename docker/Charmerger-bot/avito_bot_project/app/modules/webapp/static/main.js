// НОВЫЙ ФАЙЛ: app/modules/webapp/static/main.js

import { applyThemeStyles, openTab } from './ui.js?v=1.1.0';
import { loadMainStatus, loadAvitoAccounts, syncAccount, openManageModal, closeManageModal, saveAlias, deleteAccount, openTermsModal, closeTermsModal, acceptTerms } from './main-tab.js?v=1.2.0';
import { loadTemplates, clearTemplateForm, editTemplate, saveTemplate, deleteTemplate } from './templates.js?v=1.3.0';
import { populateAutoreplyAccountSelector, loadAutoReplies, clearAutoReplyForm, editAutoReply, saveAutoReply, deleteAutoReply } from './autoreplies.js?v=1.2.0';
import { loadForwardingRules, createInvite, deleteForwardingRule, openPermissionsModal, closePermissionsModal, savePermissions, copyInviteLink } from './forwarding.js?v=1.5.0';
import { loadTariffs, purchaseTariff } from './tariffs.js?v=1.1.0';
import { loadWallet } from './wallet.js?v=1.1.0';
import { loadSettings, saveTimezone, fullReset } from './settings.js?v=1.1.0';
import { loadAdminUsers, openPaymentsModal, closePaymentsModal, openManageUserModal, closeManageUserModal, saveUserData } from './admin.js?v=1.7.0';

window.addEventListener('load', () => {
    const tg = window.Telegram.WebApp;
    
    // 1. Сразу инициализируем WebApp
    tg.ready();
    tg.expand();
    applyThemeStyles();

    // 2. Проверяем наличие initData (ключевой момент)
    if (!tg.initData) {
        document.body.innerHTML = `
            <div style="text-align: center; padding: 20px; color: var(--tg-theme-destructive-text-color);">
                <h1>Ошибка аутентификации</h1>
                <p>Не удалось получить данные пользователя от Telegram. Пожалуйста, убедитесь, что вы открываете WebApp из клиента Telegram, и попробуйте перезапустить бота.</p>
            </div>`;
        tg.showAlert("Ошибка: данные пользователя не получены. Перезапустите WebApp.");
        return; // Прерываем выполнение, если нет данных
    }
    
    // 3. Отображаем информацию о пользователе
    const userInfo = tg.initDataUnsafe.user;
    if (userInfo) {
        const userInfoEl = document.getElementById('userInfo');
        if(userInfoEl) {
           userInfoEl.textContent = `Пользователь: ${userInfo.first_name} ${userInfo.last_name || ''} (@${userInfo.username || '...'})`;
        }
    }
        // Предполагаем, что adminId передается из шаблона
    if (userInfo && window.AdminTelegramId && userInfo.id.toString() === window.AdminTelegramId.toString()) {
        const adminTabButton = document.getElementById('adminTabBtn');
        if (adminTabButton) {
            adminTabButton.classList.remove('hidden');
        }
    }
    
    // 4. Настраиваем навигацию по вкладкам
    document.querySelectorAll('.tab-button').forEach(button => {
        button.addEventListener('click', (event) => {
            const tabName = button.dataset.tab;
            openTab(event, tabName);
            loadDataForTab(tabName);
        });
    });
    
    // Открываем первую вкладку по умолчанию
    document.querySelector('.tab-button[data-tab="mainTab"]').click();

    // 5. Настраиваем аккордеоны
    document.querySelectorAll('.accordion-header').forEach(header => {
        header.addEventListener('click', () => {
            header.classList.toggle('active');
            const content = header.nextElementSibling;
            if (content.style.maxHeight) {
                content.style.maxHeight = null;
                content.style.padding = "0 15px";
            } else {
                content.style.maxHeight = content.scrollHeight + 30 + "px";
                content.style.padding = "0 15px";
                setTimeout(() => { // Даем время на открытие
                    if(header.classList.contains('active')) { // Проверяем, что он все еще активен
                       content.style.padding = null; // Убираем паддинг после анимации
                    }
                }, 300);
            }
        });
    });
    
    // 6. Вешаем глобальные обработчики событий через делегирование
    document.body.addEventListener('click', (e) => {
        const target = e.target;
        // Управление аккаунтами
        if (target.matches('.js-sync-account')) syncAccount(target.dataset.id);
        if (target.matches('.js-manage-account')) openManageModal(parseInt(target.dataset.id));
        if (target.matches('.modal-close-btn')) closeManageModal();
        if (target.id === 'saveAliasBtn') saveAlias();
        if (target.id === 'deleteAccountBtn') deleteAccount();

        // Шаблоны
        if (target.id === 'saveTemplateBtn') saveTemplate();
        if (target.id === 'clearTemplateBtn') clearTemplateForm();
        if (target.matches('.js-edit-template')) editTemplate(parseInt(target.dataset.id));
        if (target.matches('.js-delete-template')) deleteTemplate(parseInt(target.dataset.id));

        // Автоответы
        if (target.id === 'saveAutoReplyBtn') {
            console.log("Save Auto-Reply button clicked!"); // <--- Добавляем лог
            saveAutoReply();
        }
        if (target.id === 'clearAutoReplyBtn') {
            clearAutoReplyForm();
        }
        if (target.matches('.js-edit-autoreply')) {
            // dataset.id может быть строкой, убедимся, что он правильный
            editAutoReply(target.dataset.id); 
        }
        if (target.matches('.js-delete-autoreply')) {
            deleteAutoReply(target.dataset.id);
        }
        
        // Пересылка
        if (target.id === 'createInviteBtn') createInvite();
        if (target.matches('.js-delete-forwarding')) deleteForwardingRule(target.dataset.id);

        if (target.matches('.js-copy-invite')) { copyInviteLink(target.dataset.link);}
        // Тарифы
        if (target.matches('.js-purchase-tariff')) purchaseTariff(target.dataset.id);
        
        // Настройки
        if (target.id === 'saveTimezoneBtn') saveTimezone();
        if (target.id === 'fullResetBtn') fullReset();
        // Управление правами
        if (target.matches('.js-manage-permissions')) openPermissionsModal(target.dataset.id);
        if (target.closest('.modal') && target.matches('.modal-close-btn')) closePermissionsModal();
        if (target.id === 'savePermissionsBtn') savePermissions();
        // Соглашение
        if (e.target.id === 'showTermsBtn') openTermsModal();
        if (e.target.id === 'acceptTermsBtn') acceptTerms();
        if (e.target.closest('#termsModal') && e.target.matches('.modal-close-btn')) closeTermsModal();
        // --- НОВЫЕ ОБРАБОТЧИКИ ДЛЯ АДМИНКИ ---
        if (target.matches('.js-admin-view-payments')) {
            const userId = target.dataset.userId;
            // Находим имя пользователя в той же строке таблицы для заголовка
            const userName = target.dataset.userName;
            openPaymentsModal(userId, userName);
        }

        // Закрытие модального окна платежей
        if (target.closest('#paymentsModal') && target.matches('.modal-close-btn')) {
            closePaymentsModal();
        }
        
        // --- ОБРАБОТЧИКИ УПРАВЛЕНИЯ ЮЗЕРОМ ---
        if (target.matches('.js-admin-edit-user')) {
            const userId = target.dataset.userId;
            const userName = target.dataset.userName;
            openManageUserModal(userId, userName);
        }
        if (target.id === 'saveUserBtn') {
            saveUserData();
        }
        if (target.closest('#manageUserModal') && target.matches('.modal-close-btn')) {
            closeManageUserModal();
        }
    });

    const manageUserModalOverlay = document.getElementById('manageUserModal');
    if (manageUserModalOverlay) {
        manageUserModalOverlay.addEventListener('click', (e) => {
            if (e.target === manageUserModalOverlay) closeManageUserModal();
        });
    }

    // Закрытие по оверлею
    const paymentsModalOverlay = document.getElementById('paymentsModal');
    if (paymentsModalOverlay) {
        paymentsModalOverlay.addEventListener('click', (e) => {
            if (e.target === paymentsModalOverlay) closePaymentsModal();
        });
    }

    const termsModalOverlay = document.getElementById('termsModal');
    if (termsModalOverlay) {
        termsModalOverlay.addEventListener('click', (e) => {
            if (e.target === termsModalOverlay) closeTermsModal();
        });
    }
    
    // Закрытие модального окна по клику на оверлей
    const modalOverlay = document.getElementById('manageAccountModal');
    if (modalOverlay) {
        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) closeManageModal();
        });
    }
    document.getElementById('permissionsModal')?.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closePermissionsModal();
    });
});

function loadDataForTab(tabName) {
    console.log(`Loading data for tab: ${tabName}`);
    switch (tabName) {
        case 'mainTab':
            loadMainStatus();
            loadAvitoAccounts();
            break;
        case 'templatesTab':
            loadTemplates();
            break;

        case 'autoRepliesTab':
            console.log("Triggering functions for autoRepliesTab..."); // <--- Добавьте этот лог
            populateAutoreplyAccountSelector(); // Вызывается?
            loadAutoReplies(); 
            break;
        case 'forwardingTab':
            loadForwardingRules();
            break;
        case 'tariffsTab':
            loadTariffs();
            break;
        case 'walletTab':
            loadWallet();
            break;
        case 'settingsTab':
            loadSettings();
            break;
        case 'adminTab':
            loadAdminUsers();
            break;
    }
}