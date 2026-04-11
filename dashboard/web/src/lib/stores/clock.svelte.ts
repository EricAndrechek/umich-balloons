// Shared reactive clock. Reading `clock.now` in a component template causes
// it to re-render every second, which makes `formatAge()` outputs tick live
// without needing to poll the API.

class Clock {
	now = $state(Date.now());

	constructor() {
		if (typeof window !== 'undefined') {
			setInterval(() => {
				this.now = Date.now();
			}, 1000);
		}
	}
}

export const clock = new Clock();
