/*
 * The production manifest deliberately does not register a content script.
 * The regular Meta AI site remains a user-controlled handoff. This file is
 * shared only with the isolated local mock test manifest under tests/.
 */
(() => {
  const allowed = location.origin === 'http://127.0.0.1:8766' && location.pathname === '/__vintendo_mock__/';
  if (!allowed) return;
  chrome.runtime.onMessage.addListener((message, _sender, reply) => {
    if (message?.type !== 'mockGenerate') return;
    (async () => {
      const prompt = document.querySelector('#prompt');
      const photosInput = document.querySelector('#photos');
      if (!prompt || !photosInput || !document.querySelector('#submit')) throw new Error('Mock page controls are missing.');
      prompt.value = message.prompt;
      prompt.dispatchEvent(new Event('input', {bubbles:true}));
      const transfer = new DataTransfer();
      for (const item of message.photos) {
        const response = await fetch(item.dataUrl);
        const blob = await response.blob();
        transfer.items.add(new File([blob], item.name, {type:item.mime || blob.type || 'image/jpeg'}));
      }
      photosInput.files = transfer.files;
      photosInput.dispatchEvent(new Event('change', {bubbles:true}));
      document.querySelector('#submit').click();
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {observer.disconnect();reject(new Error('Mock generation timed out.'));}, 5000);
        const done = () => {
          if (!document.querySelector('#completed-image')?.src || !document.querySelector('#download')?.href) return;
          clearTimeout(timer);observer.disconnect();resolve();
        };
        const observer = new MutationObserver(done);
        observer.observe(document.querySelector('#result'), {childList:true,subtree:true,attributes:true});
        done();
      });
      document.querySelector('#download').click();
      return {submitted:true, attachedPhotos:photosInput.files.length, downloaded:true};
    })().then(reply).catch(error => reply({error:String(error.message || error)}));
    return true;
  });
})();
