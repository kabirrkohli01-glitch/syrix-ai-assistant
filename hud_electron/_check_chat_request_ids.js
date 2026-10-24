'use strict';

const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'syrix-bridge.js'), 'utf8');
const chatSource = fs.readFileSync(path.join(__dirname, 'syrix-primary-chat.jsx'), 'utf8');

function requireSource(pattern, message) {
  if (!pattern.test(source)) throw new Error(message);
}

requireSource(
  /const _chatRequestSession = Date\.now\(\)\.toString\(36\)/,
  'chat request ids must carry a renderer-session prefix'
);
requireSource(
  /const requestId = _chatRequestSession \+ '-' \+ \(\+\+_chatRequestSeq\)\.toString\(36\)/,
  'chat request ids must combine the session prefix and monotonic sequence'
);
requireSource(
  /String\(p\.id\) !== rid/,
  'cancel handling must compare opaque string request ids'
);
requireSource(
  /lineRole\(candidate\) === 'user'[\s\S]*lineRequestId\(candidate\) === rid/,
  'same-id assistant dedupe must anchor to the matching user turn'
);
requireSource(
  /existingTs >= userTurnTs - 0\.5/,
  'same-id assistant dedupe must reject replies from earlier reused-id turns'
);
requireSource(
  /send\('cancel_hud_chat',[\s\S]{0,260}superseded_by_new_turn/,
  'a superseded renderer request must be cancelled at the backend'
);
requireSource(
  /const _chatCancelledTurns = new Map\(\)/,
  'cancelled request ownership must use an ordered TTL map'
);
requireSource(
  /if \(p && rid && String\(p\.id\) === rid && !isChatRequestComplete\(p\)\)/,
  'terminal status must require the exact non-empty pending request id'
);
requireSource(
  /function findAssistantReplyForPending\(pending\)[\s\S]{0,900}if \(!rid \|\| String\(rid\) !== String\(pending\.id\)\) continue;[\s\S]{0,300}return null;/,
  'pending reply lookup must not fall back to a rid-less assistant line'
);
requireSource(
  /isExplicitInterimAssistantMessage\(_chatLines\[existingIdx\]\)[\s\S]{0,180}newText\.length > exText\.length \+ 12/,
  'same-id replacement must require an explicitly interim stored line'
);

if (!/return !!\(activeRid && lineRid && activeRid === lineRid\);/.test(chatSource)) {
  throw new Error('pending-turn completion must require the exact non-empty request id');
}
if (/\(Date\.now\(\) - sentMs\) < 20000/.test(chatSource)) {
  throw new Error('rid-less pending completion grace window must not return');
}
if (!/function onChatToken\(ev\)[\s\S]{0,1000}const rid = String\(d\.rid \|\| d\.request_id \|\| ''\)\.trim\(\);[\s\S]{0,1000}if \(!rid \|\| !activeRid \|\| rid !== activeRid\) return;/.test(chatSource)) {
  throw new Error('stream tokens and done frames must require the exact active request id');
}
if (!/function clearPendingTurn\([\s\S]{0,700}!rid \|\| !activeRid \|\| rid !== activeRid[\s\S]{0,700}setStreamingText\(''\)/.test(chatSource)) {
  throw new Error('terminal and cancellation cleanup must clear the owned streaming bubble');
}
if (!/function terminalMatches\([\s\S]{0,260}return !!\(rid && activeRid && rid === activeRid\);/.test(chatSource)) {
  throw new Error('terminal events must require the exact active request id');
}
if (!/if \(!evRid \|\| !activeRid \|\| evRid !== activeRid\) return;/.test(chatSource)) {
  throw new Error('activity-feed events must require the exact active request id');
}
if (!/const stillOwnsTurn = !!\(bridgeRequestId && activeRidAtCompletion === bridgeRequestId\);[\s\S]{0,700}!hasFinalReplyForRequest\(bridgeRequestId\)/.test(chatSource)) {
  throw new Error('promise delivery must use its immutable request id and exact-once guard');
}

if (/const requestId = \+\+_chatRequestSeq/.test(source)) {
  throw new Error('renderer-restart-colliding numeric request ids returned');
}

console.log('chat request id checks passed');
