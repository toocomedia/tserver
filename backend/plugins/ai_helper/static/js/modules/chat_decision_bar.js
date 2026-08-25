/** Bridge for the setup interview card. Rendering lives in chat_setup_interview.js. */
(function () {
  "use strict";

  window.AiHelperDecisionBar = {
    containerEl: null,
    onSelectCallback: null,

    init: function (containerEl, onSelectCallback) {
      this.containerEl = containerEl;
      this.onSelectCallback = onSelectCallback;
      window.AiHelperSetupInterview.init(containerEl, onSelectCallback);
    },

    hide: function () {
      window.AiHelperSetupInterview.hide();
    },

    extractAndShow: function (text) {
      window.AiHelperSetupInterview.extractAndShow(text);
    },
  };
})();
