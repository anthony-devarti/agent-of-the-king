const form = document.getElementById('availability-form');
const status = document.getElementById('status');
const saveButton = document.getElementById('save-button');
const userIdInput = document.getElementById('user-id');
const hiddenUserIdInput = document.getElementById('user-id-hidden');
const selectedSlotsInput = document.getElementById('selected-slots');
const boxes = Array.from(document.querySelectorAll('.slot-box'));
const isHeatmap = boxes.some((box) => box.dataset.heatmap === 'true');
const dayHeaders = Array.from(document.querySelectorAll('.grid-header:not(.time-header)'));
const sectionHeaders = Array.from(document.querySelectorAll('.section-header'));
const userFilterContainer = document.getElementById('user-availability-data');
const recommendationSummary = document.getElementById('recommendation-summary');
const discordUserIdPattern = /^\d{17,20}$/;
const userFilterState = new Map();
const userLockState = new Map();
const userFilterData = JSON.parse(userFilterContainer?.dataset.users || '[]');
const slotUserMap = JSON.parse(userFilterContainer?.dataset.slotUsers || '{}');
const sessionLengthHours = Math.max(0.5, parseFloat(recommendationSummary?.dataset.sessionLengthHours || '4') || 4);
const sessionSlotCount = Math.max(1, Math.round(sessionLengthHours * 2));
const dragState = {
  active: false,
  pointerId: null,
  paintValue: false,
  lastBox: null,
};

function getSelectedSlots() {
  return boxes
    .filter((box) => box.getAttribute('data-active') === 'true')
    .map((box) => `${box.dataset.day}:${box.dataset.slot}`)
    .join(',');
}

function setStatus(message, isError = false) {
  status.textContent = message;
  status.style.color = isError ? '#fca5a5' : '#93c5fd';
}

function setSavingState(isSaving) {
  if (!saveButton) {
    return;
  }

  saveButton.disabled = isSaving;
  saveButton.textContent = isSaving ? 'Saving…' : 'Save';
}

function getEditorUserId() {
  return (hiddenUserIdInput?.value || userIdInput?.value || '').trim();
}

function isDiscordUserId(value) {
  return discordUserIdPattern.test(String(value || '').trim());
}

function syncSelectedSlots() {
  selectedSlotsInput.value = getSelectedSlots();
}

function updateDayHeaderStates() {
  const dayStates = new Map();

  dayHeaders.forEach((header) => {
    const day = header.dataset.day;
    const hasSelection = boxes.some((box) => box.dataset.day === day && box.getAttribute('data-active') === 'true');
    dayStates.set(day, hasSelection);
  });

  dayHeaders.forEach((header) => {
    const day = header.dataset.day;
    const isEmpty = !dayStates.get(day);
    header.classList.toggle('is-empty', isEmpty);
    header.setAttribute('aria-label', isEmpty ? `${day} has no selections` : `${day} has selections`);
  });
}

function updateSectionHeaderStates() {
  sectionHeaders.forEach((header) => {
    const group = header.dataset.group;
    const sectionBoxes = boxes.filter((box) => {
      if (group === 'morning') {
        return parseInt(box.dataset.slot.slice(0, 2), 10) >= 3 && parseInt(box.dataset.slot.slice(0, 2), 10) < 12;
      }
      if (group === 'afternoon') {
        return parseInt(box.dataset.slot.slice(0, 2), 10) >= 12 && parseInt(box.dataset.slot.slice(0, 2), 10) < 18;
      }
      if (group === 'night') {
        return parseInt(box.dataset.slot.slice(0, 2), 10) < 3 || parseInt(box.dataset.slot.slice(0, 2), 10) >= 18;
      }
      return false;
    });

    const hasSelection = sectionBoxes.some((box) => box.getAttribute('data-active') === 'true');
    const status = header.querySelector('.section-status');
    if (status) {
      status.textContent = hasSelection ? '' : 'Not available';
      status.classList.toggle('is-hidden', hasSelection);
    }

    const indicators = Array.from(header.querySelectorAll('.section-indicator'));
    indicators.forEach((indicator) => {
      const day = indicator.dataset.day;
      const hasDaySelection = sectionBoxes.some((box) => box.dataset.day === day && box.getAttribute('data-active') === 'true');
      indicator.classList.toggle('is-active', hasDaySelection);
    });
  });
}

function setBoxActive(box, isActive) {
  if (isHeatmap) {
    return;
  }

  const nextValue = isActive ? 'true' : 'false';
  if (box.getAttribute('data-active') === nextValue) {
    return;
  }

  box.setAttribute('data-active', nextValue);
  syncSelectedSlots();
  updateDayHeaderStates();
  updateSectionHeaderStates();
  setStatus('You have unsaved changes');
}

function toggleBox(box) {
  const isActive = box.getAttribute('data-active') === 'true';
  setBoxActive(box, !isActive);
}

function getActiveUserIds() {
  const activeUserIds = [];
  userFilterState.forEach((isActive, userId) => {
    if (isActive) {
      activeUserIds.push(userId);
    }
  });
  return activeUserIds;
}

function getVisibleCount(box, activeUserIds) {
  const slotId = `${box.dataset.day}:${box.dataset.slot}`;
  const contributingUsers = slotUserMap[slotId] || [];
  if (!activeUserIds.length) {
    return contributingUsers.length;
  }
  return activeUserIds.filter((userId) => contributingUsers.includes(userId)).length;
}

function applyHeatmapStyles() {
  const activeUserIds = getActiveUserIds();
  const visibleMaxCount = Math.max(
    1,
    ...boxes.map((box) => getVisibleCount(box, activeUserIds)),
  );

  boxes.forEach((box) => {
    const count = getVisibleCount(box, activeUserIds);
    if (!count) {
      box.style.background = 'rgba(15, 23, 42, 0.9)';
      box.style.boxShadow = 'none';
      return;
    }

    const normalized = Math.min(1, count / visibleMaxCount);
    const blue = Math.round(37 + normalized * 140);
    const green = Math.round(99 + normalized * 70);
    const alpha = 0.28 + normalized * 0.6;
    const glow = 0.16 + normalized * 0.24;
    box.style.background = `rgba(${blue}, ${green}, 255, ${alpha})`;
    box.style.boxShadow = `inset 0 0 0 1px rgba(255,255,255,${glow})`;
  });

  updateRecommendationStyles();
}

function formatSlotRange(slot) {
  const [hours, minutes] = slot.split(':').map(Number);
  const startMinutes = hours * 60 + minutes;
  const endMinutes = startMinutes + (sessionLengthHours * 60);
  const endHour = (Math.floor(endMinutes / 60) % 24 + 24) % 24;
  const endMinute = endMinutes % 60;
  const pad = (value) => String(value).padStart(2, '0');

  const formatClock = (value, valueMinutes) => {
    const hour12 = value % 12 || 12;
    const suffix = value >= 12 ? 'PM' : 'AM';
    return `${hour12}:${pad(valueMinutes)}${suffix}`;
  };

  const startHour = hours % 12 || 12;
  const startSuffix = hours >= 12 ? 'PM' : 'AM';
  const endSuffix = endHour >= 12 ? 'PM' : 'AM';
  return `${startHour}:${pad(minutes)}${startSuffix} to ${endHour % 12 || 12}:${pad(endMinute)}${endSuffix}`;
}

function getLockedUserIds(activeUserIds) {
  return Array.from(userLockState.entries())
    .filter(([userId, isLocked]) => isLocked && activeUserIds.includes(userId))
    .map(([userId]) => userId);
}

function getRecommendation(activeUserIds) {
  if (!activeUserIds.length) {
    return null;
  }

  const days = [...new Set(boxes.map((box) => box.dataset.day))];
  const daySlots = Array.from(new Set(boxes.map((box) => box.dataset.slot))).sort((a, b) => a.localeCompare(b));
  const recommendations = [];
  const lockedUserIds = getLockedUserIds(activeUserIds);

  days.forEach((day) => {
    for (let start = 0; start <= daySlots.length - sessionSlotCount; start += 1) {
      const windowSlots = daySlots.slice(start, start + sessionSlotCount);
      const matchingUsers = activeUserIds.filter((userId) => {
        return windowSlots.every((slot) => {
          const slotId = `${day}:${slot}`;
          return (slotUserMap[slotId] || []).includes(userId);
        });
      });

      const attendsLockedUsers = lockedUserIds.every((userId) => {
        return windowSlots.every((slot) => {
          const slotId = `${day}:${slot}`;
          return (slotUserMap[slotId] || []).includes(userId);
        });
      });

      const attendingCount = matchingUsers.length;
      if (!attendsLockedUsers || attendingCount <= 1) {
        continue;
      }

      recommendations.push({
        day,
        startSlot: windowSlots[0],
        endSlot: windowSlots[windowSlots.length - 1],
        count: attendingCount,
        slotIds: windowSlots.map((slot) => `${day}:${slot}`),
        matchingUsers,
      });
    }
  });

  if (!recommendations.length) {
    return null;
  }

  const highestCount = Math.max(...recommendations.map((recommendation) => recommendation.count));
  return recommendations.filter((recommendation) => recommendation.count === highestCount);
}

function updateRecommendationStyles() {
  const activeUserIds = getActiveUserIds();
  const recommendation = getRecommendation(activeUserIds);

  const recommendations = Array.isArray(recommendation) ? recommendation : recommendation ? [recommendation] : [];

  boxes.forEach((box) => {
    const isRecommended = recommendations.some((entry) => entry.slotIds.includes(`${box.dataset.day}:${box.dataset.slot}`)) || false;
    box.classList.toggle('is-recommended', isRecommended);
    box.setAttribute('data-recommended', isRecommended ? 'true' : 'false');
    if (isRecommended) {
      box.style.outline = '2px solid rgba(250, 204, 21, 0.82)';
      box.style.outlineOffset = '-2px';
      box.style.borderColor = '#fde68a';
    } else {
      box.style.outline = '';
      box.style.outlineOffset = '';
      box.style.borderColor = '';
    }
  });

  if (!recommendationSummary) {
    return;
  }

  if (!recommendations.length) {
    recommendationSummary.innerHTML = `<span class="recommendation-label">Live recommendation</span><strong>No strong shared ${sessionLengthHours}-hour window is available for the selected players.</strong>`;
    return;
  }

  const firstRecommendation = recommendations[0];
  const label = `${firstRecommendation.day}: the strongest shared window for the selected players is a full ${sessionLengthHours}-hour block from ${formatSlotRange(firstRecommendation.startSlot)}. ${firstRecommendation.count} players match that window.`;
  recommendationSummary.innerHTML = `<span class="recommendation-label">Live recommendation</span><strong>${label}</strong>`;
}

function renderUserFilters() {
  if (!userFilterContainer) {
    return;
  }

  const users = userFilterData;
  userFilterContainer.innerHTML = '';
  const grouped = new Map();

  users.forEach((user) => {
    let isActive = userFilterState.has(user.id) ? userFilterState.get(user.id) : user.active !== false;
    const isLocked = userLockState.has(user.id) ? userLockState.get(user.id) : false;
    if (isLocked && !isActive) {
      isActive = true;
    }
    userFilterState.set(user.id, isActive);
    userLockState.set(user.id, isLocked);

    const groupKey = user.group || 'B';
    const groupLabel = user.group_label || (groupKey === 'A'
      ? 'Game participants'
      : 'Other users with availability');
    if (!grouped.has(groupKey)) {
      grouped.set(groupKey, { label: groupLabel, users: [] });
    }
    grouped.get(groupKey).users.push(user);
  });

  const orderedGroups = ['A', 'B', ...Array.from(grouped.keys()).filter((key) => key !== 'A' && key !== 'B')];
  orderedGroups.forEach((groupKey) => {
    const bucket = grouped.get(groupKey);
    if (!bucket) {
      return;
    }

    const wrapper = document.createElement('section');
    wrapper.className = 'user-group';

    const heading = document.createElement('h3');
    heading.className = 'user-group-title';
    heading.textContent = bucket.label;
    wrapper.appendChild(heading);

    const row = document.createElement('div');
    row.className = 'user-filter-row';

    bucket.users.forEach((user) => {
      const isActive = userFilterState.get(user.id);
      const isLocked = userLockState.get(user.id);
      const isSelectable = user.selectable !== false;

      const chip = document.createElement('div');
      chip.className = `user-chip ${isActive ? 'is-selected' : 'is-deselected'} ${isLocked ? 'is-locked' : 'is-unlocked'} ${isSelectable ? 'is-selectable' : 'is-unavailable'}`;

      const toggleButton = document.createElement('button');
      toggleButton.className = 'user-toggle';
      toggleButton.type = 'button';
      toggleButton.dataset.userId = user.id;
      toggleButton.disabled = !isSelectable;
      toggleButton.setAttribute('aria-pressed', String(isActive));
      toggleButton.setAttribute('aria-disabled', String(!isSelectable));
      toggleButton.setAttribute('aria-label', `${isActive ? 'Deselect' : 'Select'} ${user.name}`);
      const toggleDot = document.createElement('span');
      toggleDot.className = `user-toggle-dot ${isActive ? 'is-active' : 'is-dimmed'}`;
      const toggleLabel = document.createElement('span');
      toggleLabel.className = 'user-toggle-label';
      toggleLabel.textContent = String(user.name || user.id || 'Unknown user');
      toggleButton.appendChild(toggleDot);
      toggleButton.appendChild(toggleLabel);
      toggleButton.addEventListener('click', () => {
        if (!isSelectable) {
          return;
        }
        const nextValue = !userFilterState.get(user.id);
        if (!nextValue && userLockState.get(user.id)) {
          setStatus(`Unlock ${user.name} before deselecting`, true);
          renderUserFilters();
          return;
        }
        userFilterState.set(user.id, nextValue);
        renderUserFilters();
        applyHeatmapStyles();
      });

      const lockButton = document.createElement('button');
      lockButton.className = 'user-lock-toggle';
      lockButton.type = 'button';
      lockButton.dataset.userId = user.id;
      lockButton.disabled = !isSelectable;
      lockButton.setAttribute('aria-pressed', String(isLocked));
      lockButton.setAttribute('aria-disabled', String(!isSelectable));
      lockButton.setAttribute('aria-label', `${isLocked ? 'Unlock' : 'Lock'} ${user.name}`);
      lockButton.setAttribute('title', isLocked ? 'Unlock this user for recommendations' : 'Lock this user for recommendations');
      lockButton.innerHTML = `<span class="user-lock-icon" aria-hidden="true">${isLocked ? '🔒' : '🔓'}</span>`;
      lockButton.addEventListener('click', () => {
        if (!isSelectable) {
          return;
        }
        const nextValue = !userLockState.get(user.id);
        userLockState.set(user.id, nextValue);
        if (nextValue) {
          userFilterState.set(user.id, true);
        }
        renderUserFilters();
        applyHeatmapStyles();
      });

      chip.appendChild(toggleButton);
      chip.appendChild(lockButton);
      row.appendChild(chip);
    });

    wrapper.appendChild(row);
    userFilterContainer.appendChild(wrapper);
  });
}

async function loadAvailability() {
  if (isHeatmap) {
    renderUserFilters();
    applyHeatmapStyles();
    updateDayHeaderStates();
    updateSectionHeaderStates();
    updateRecommendationStyles();
    return;
  }

  const userId = getEditorUserId();
  if (!isDiscordUserId(userId)) {
    setStatus('Invalid user ID. Open this page from Discord using /availability.', true);
    if (saveButton) {
      saveButton.disabled = true;
    }
    return;
  }
  const params = new URLSearchParams({ user_id: userId });
  const response = await fetch(`/availability?${params.toString()}`);
  if (!response.ok) {
    setStatus('Unable to load availability.', true);
    return;
  }
  const data = await response.json();
  boxes.forEach((box) => {
    const slotId = `${box.dataset.day}:${box.dataset.slot}`;
    const isSelected = data.slots.includes(slotId);
    box.setAttribute('data-active', isSelected ? 'true' : 'false');
  });
  syncSelectedSlots();
  updateDayHeaderStates();
  updateSectionHeaderStates();
}

if (form) {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const userId = getEditorUserId();
    if (!isDiscordUserId(userId)) {
      setStatus('Invalid user ID. Open this page from Discord using /availability.', true);
      return;
    }
    syncSelectedSlots();
    setSavingState(true);
    setStatus('Saving...');

    try {
      const formData = new FormData(form);
      const response = await fetch('/availability', {
        method: 'POST',
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok) {
        setStatus('Unable to save availability.', true);
        return;
      }
      setStatus(`Saved ${payload.saved} slots.`);
    } finally {
      setSavingState(false);
    }
  });
}

boxes.forEach((box) => {
  box.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'mouse' && event.button !== 0) {
      return;
    }

    event.preventDefault();
    dragState.active = true;
    dragState.pointerId = event.pointerId;
    dragState.paintValue = box.getAttribute('data-active') !== 'true';
    dragState.lastBox = box;
    setBoxActive(box, dragState.paintValue);
    box.setPointerCapture?.(event.pointerId);
  });

  box.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggleBox(box);
    }
  });
});

document.addEventListener('pointermove', (event) => {
  if (!dragState.active || event.pointerId !== dragState.pointerId) {
    return;
  }

  const targetBox = document.elementFromPoint(event.clientX, event.clientY)?.closest('.slot-box');
  if (!targetBox || targetBox === dragState.lastBox) {
    return;
  }

  dragState.lastBox = targetBox;
  setBoxActive(targetBox, dragState.paintValue);
}, { passive: false });

document.addEventListener('pointerup', (event) => {
  if (!dragState.active || event.pointerId !== dragState.pointerId) {
    return;
  }

  dragState.active = false;
  dragState.pointerId = null;
  dragState.lastBox = null;
}, { passive: false });

document.addEventListener('pointercancel', (event) => {
  if (!dragState.active || event.pointerId !== dragState.pointerId) {
    return;
  }

  dragState.active = false;
  dragState.pointerId = null;
  dragState.lastBox = null;
}, { passive: false });

sectionHeaders.forEach((header) => {
  const toggleSection = () => {
    const sectionBlock = header.closest('.section-block');
    const body = sectionBlock?.querySelector('.section-body');
    if (!body) {
      return;
    }

    const shouldCollapse = body.classList.contains('is-collapsed') ? false : true;
    body.classList.toggle('is-collapsed', shouldCollapse);
    header.setAttribute('aria-expanded', String(!shouldCollapse));
  };

  header.addEventListener('click', toggleSection);
  header.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggleSection();
    }
  });
});

loadAvailability().catch(() => setStatus('Unable to load availability.', true));
