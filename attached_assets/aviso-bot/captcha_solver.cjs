'use strict';

/**
 * Captcha solver stub — watch tasks are disabled per instructions.
 * hCaptcha solving is not needed for surf/like/subscribe tasks.
 */
async function solveHCaptcha(_page, _siteKey) {
  throw new Error('[captcha] hCaptcha solving not implemented — watch tasks are disabled');
}

module.exports = { solveHCaptcha };
