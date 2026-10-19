// Canonical document objects never enter Excalidraw, which may mutate metadata.
// Animation frames reuse only their independent editor copies.
export class StrokeScene {
  constructor(copy = (value) => structuredClone(value)) { this.copy = copy; this.reset() }
  reset() { this.source = null; this.elements = null }
  base(elements, refresh = false) {
    if (refresh || this.source !== elements) {
      this.source = elements
      this.elements = this.copy(elements)
    }
    return this.elements
  }
  frame(elements, stroke) { return [...this.base(elements), this.copy(stroke)] }
}
