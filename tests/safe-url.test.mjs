import test from 'node:test';
import assert from 'node:assert/strict';

import { safeHttpUrl } from '../static/safe-url.js';

test('absolute http and https URLs are allowed', () => {
  assert.equal(safeHttpUrl('https://example.com/visit'), 'https://example.com/visit');
  assert.equal(safeHttpUrl('http://example.com'), 'http://example.com/');
});

test('script, data, relative, and malformed URLs are refused', () => {
  for (const value of [
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    ' javascript:alert(1)',
    'java\nscript:alert(1)',
    'java\tscript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    '//evil.example/x',
    '/relative',
    'not a url',
    '',
    null,
    undefined,
    42,
  ]) {
    assert.equal(safeHttpUrl(value), null, String(value));
  }
});
