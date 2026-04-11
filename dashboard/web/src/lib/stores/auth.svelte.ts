const STORAGE_KEY = 'umbg_admin_pw';

class Auth {
	password = $state<string | null>(null);

	constructor() {
		if (typeof window !== 'undefined') {
			this.password = window.localStorage.getItem(STORAGE_KEY);
		}
	}

	set(pw: string) {
		this.password = pw;
		if (typeof window !== 'undefined') {
			window.localStorage.setItem(STORAGE_KEY, pw);
		}
	}

	clear() {
		this.password = null;
		if (typeof window !== 'undefined') {
			window.localStorage.removeItem(STORAGE_KEY);
		}
	}
}

export const auth = new Auth();
