// static/templates.js
import { apiCall } from './api.js';
import { escapeHtml, escapeJs } from './ui.js';

const tg = window.Telegram.WebApp;
let loadedTemplates = [];

export async function loadTemplates() {
    const listDiv = document.getElementById('templatesList');
    listDiv.innerHTML = `<p class="status-loading">Загрузка шаблонов...</p>`;
    const templates = await apiCall('/api/templates');
    loadedTemplates = templates;

    listDiv.innerHTML = '';
    if (templates && Array.isArray(templates) && templates.length > 0) {
        templates.sort((a, b) => a.name.localeCompare(b.name)).forEach(t => {
            const div = document.createElement('div');
            div.className = 'item-card';
            div.innerHTML = `
                <h4>${escapeHtml(t.name)}</h4>
                <p class="template-text-preview">${escapeHtml(t.text.substring(0, 150))}${t.text.length > 150 ? '...' : ''}</p>
                <div class="item-actions">
                    <button class="js-edit-template" data-id="${t.id}">Изменить</button>
                    <button class="danger js-delete-template" data-id="${t.id}">Удалить</button>
                </div>`;
            listDiv.appendChild(div);
        });
    } else if (templates && Array.isArray(templates)) { 
        listDiv.innerHTML = '<p>Шаблоны не найдены.</p>';
    } else {
        listDiv.innerHTML = '<p class="status-error">Не удалось загрузить шаблоны.</p>';
    }
}

export function clearTemplateForm() {
    document.getElementById('templateId').value = '';
    document.getElementById('templateName').value = '';
    document.getElementById('templateText').value = '';
    document.querySelector('#templatesTab .accordion-header').textContent = 'Добавить новый шаблон';
}

export function editTemplate(id) {
    const template = loadedTemplates.find(t => t.id === id);
    if (!template) return;

    document.getElementById('templateId').value = template.id;
    document.getElementById('templateName').value = template.name;
    document.getElementById('templateText').value = template.text;
    
    const header = document.querySelector('#templatesTab .accordion-header');
    header.textContent = 'Изменить шаблон';
    if (!header.classList.contains('active')) {
        header.click();
    }
    document.getElementById('templateName').focus();
}

export async function saveTemplate() {
    const id = document.getElementById('templateId').value;
    const name = document.getElementById('templateName').value.trim();
    const text = document.getElementById('templateText').value.trim();

    if (!name || !text) {
        tg.showAlert('Название и текст шаблона обязательны.');
        return;
    }
    
    const payload = { name, text };
    const endpoint = id ? `/api/templates/${id}` : '/api/templates';
    const method = id ? 'PUT' : 'POST';
    
    const result = await apiCall(endpoint, method, payload);
    if (result) {
        tg.HapticFeedback.notificationOccurred('success');
        clearTemplateForm();
        document.querySelector('#templatesTab .accordion-header').click();
        loadTemplates();
    }
}

export function deleteTemplate(id) {
    tg.showConfirm(`Вы уверены, что хотите удалить этот шаблон?`, async (confirmed) => {
        if (confirmed) {
            const result = await apiCall(`/api/templates/${id}`, 'DELETE');
            if (result === true) {
                tg.HapticFeedback.notificationOccurred('success');
                loadTemplates();
                clearTemplateForm();
            }
        }
    });
}