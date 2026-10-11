import "@testing-library/jest-dom";

// react-router v7 depends on the WHATWG encoding globals, which the jsdom
// environment used by react-scripts 5 does not provide.
import { TextEncoder, TextDecoder } from "util";
if (!(global as any).TextEncoder) (global as any).TextEncoder = TextEncoder;
if (!(global as any).TextDecoder) (global as any).TextDecoder = TextDecoder;

// jsdom lacks several browser APIs that Radix UI primitives touch. Stub the
// minimal surface the components under test rely on so tests stay deterministic.
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!(global as any).ResizeObserver) {
  (global as any).ResizeObserver = ResizeObserverStub;
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {};
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}

// AttachmentViewer revokes object URLs on cleanup. jsdom does not implement
// the object-URL API, so stub it to keep unmount deterministic in tests.
if (typeof URL.createObjectURL !== "function") {
  (URL as any).createObjectURL = () => "blob:mock-object-url";
}
if (typeof URL.revokeObjectURL !== "function") {
  (URL as any).revokeObjectURL = () => {};
}

// Workspace auto-scrolls its message list; jsdom does not implement scrollTo.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}
