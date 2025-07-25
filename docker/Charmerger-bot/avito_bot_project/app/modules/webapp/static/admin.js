// /app/modules/webapp/static/admin.js

import { apiCall } from './api.js';
import { escapeHtml } from './ui.js';

export async function loadAdminUsers() {
    const tbody = document.getElementById('adminUsersTbody');
    if (!tbody) {
        console.error("ADMIN: Element with ID 'adminUsersTbody' not found in HTML.");
        return;
    }

    tbody.innerHTML = '<tr><td colspan="6" class="status-loading">Загрузка пользователей...</td></tr>';
    
    // Добавляем отладочный лог перед вызовом API
    console.log("ADMIN: Requesting data from /api/admin/users...");
    const users = await apiCall('/api/admin/users');
    
    // Добавляем отладочный лог после получения ответа
    console.log("ADMIN: Received users data from API:", users);

    // Проверяем, что ответ - это массив
    if (users && Array.isArray(users)) {
        if (users.length > 0) {
            // Если массив не пустой, рендерим таблицу
            tbody.innerHTML = '';
            users.forEach(user => {
                const tr = document.createElement('tr');
                // Используем escapeHtml для всех данных от пользователя
                tr.innerHTML = `
                    <td>${user.id}</td>
                    <td>${escapeHtml(user.full_name)}<br><small>TG ID: ${user.telegram_id}</small></td>
                    <td>${escapeHtml(user.tariff_plan)}</td>
                    <td>${escapeHtml(user.expires_at)}</td>
                    <td>${user.balance.toFixed(2)} ₽</td>
                    <td>
                        <button class="small-btn js-admin-edit-user" data-user-id="${user.id}" data-user-name="${escapeHtml(user.full_name)}">Управлять</button>
                        <button class="small-btn js-admin-view-payments" data-user-id="${user.id}" data-user-name="${escapeHtml(user.full_name)}">Платежи</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        } else {
            // Если массив пустой, сообщаем об этом
            console.log("ADMIN: API returned an empty array. Displaying 'No users found'.");
            tbody.innerHTML = '<tr><td colspan="6">Пользователи не найдены.</td></tr>';
        }
    } else {
        // Если ответ не пришел или это не массив, показываем ошибку
        console.error("ADMIN: Failed to load users or the response was not an array.");
        tbody.innerHTML = '<tr><td colspan="6" class="status-error">Ошибка при загрузке списка пользователей.</td></tr>';
    }
}
export function closePaymentsModal() {
    document.getElementById('paymentsModal').classList.add('hidden');
}

export async function openPaymentsModal(userId, userName) {
    const modal = document.getElementById('paymentsModal');
    const title = document.getElementById('paymentsModalTitle');
    const tbody = document.getElementById('paymentsTbody');
    
    title.textContent = `Платежи: ${escapeHtml(userName)}`;
    tbody.innerHTML = '<tr><td colspan="4" class="status-loading">Загрузка...</td></tr>';
    modal.classList.remove('hidden');

    const transactions = await apiCall(`/api/admin/users/${userId}/transactions`);
    
    if (transactions && Array.isArray(transactions)) {
        if (transactions.length > 0) {
            tbody.innerHTML = '';
            transactions.forEach(tx => {
                const amountClass = tx.amount > 0 ? 'amount-plus' : 'amount-minus';
                const amountSign = tx.amount > 0 ? '+' : '';
                
                const tr = document.createElement('tr');

                // ---!!! ИЗМЕНЕНИЕ ЗДЕСЬ: Добавляем title="..." !!!---
                tr.innerHTML = `
                    <td data-label="Дата">${escapeHtml(tx.timestamp)}</td>
                    <td data-label="Описание" title="${escapeHtml(tx.full_description)}">${escapeHtml(tx.description)}</td>
                    <td data-label="Сумма" class="amount ${amountClass}">${amountSign}${tx.amount.toFixed(2)} ₽</td>
                    <td data-label="Баланс после">${tx.balance_after.toFixed(2)} ₽</td>
                `;
                tbody.appendChild(tr);
            });
        } else {
            tbody.innerHTML = '<tr><td colspan="4">Транзакции не найдены.</td></tr>';
        }
    } else {
        tbody.innerHTML = '<tr><td colspan="4" class="status-error">Ошибка загрузки.</td></tr>';
    }
}

export function closeManageUserModal() {
    document.getElementById('manageUserModal').classList.add('hidden');
}

export async function openManageUserModal(userId, userName) {
    const modal = document.getElementById('manageUserModal');
    const title = document.getElementById('manageUserModalTitle');
    
    title.textContent = `Управляем: ${escapeHtml(userName)}`;
    
    // Очищаем форму перед загрузкой
    document.getElementById('manageUserForm').reset();
    document.getElementById('userTariffSelect').innerHTML = '<option>Загрузка...</option>';

    modal.classList.remove('hidden');

    const userData = await apiCall(`/api/admin/users/${userId}`);
    if (!userData) {
        title.textContent = "Ошибка загрузки данных";
        return;
    }
    
    document.getElementById('editingUserId').value = userData.id;
    
    // Заполняем селектор тарифов
    const tariffSelect = document.getElementById('userTariffSelect');
    tariffSelect.innerHTML = '';
    userData.available_tariffs.forEach(tariff => {
        const option = new Option(tariff.name, tariff.id);
        if (tariff.id === userData.tariff_plan) {
            option.selected = true;
        }
        tariffSelect.add(option);
    });
    
    // Заполняем остальные поля
    document.getElementById('userBalanceInput').value = userData.balance.toFixed(2);
    document.getElementById('userExpiresAtInput').value = userData.expires_at || '';
}

export async function saveUserData() {
    const userId = document.getElementById('editingUserId').value;
    if (!userId) return;

    // Собираем данные из формы
    const payload = {
        tariff_plan: document.getElementById('userTariffSelect').value,
        balance: parseFloat(document.getElementById('userBalanceInput').value) || 0,
        expires_at: document.getElementById('userExpiresAtInput').value || null,
        add_to_balance: parseFloat(document.getElementById('userAddToBalanceInput').value) || 0
    };

    const result = await apiCall(`/api/admin/users/${userId}`, 'POST', payload);
    if (result && result.success) {
        Telegram.WebApp.HapticFeedback.notificationOccurred('success');
        Telegram.WebApp.showAlert(result.message);
        closeManageUserModal();
        loadAdminUsers(); // Обновляем таблицу
    }
}