package lora

import "testing"

// TestHasUsablePosition covers the source-side pre-fix filter. The rule
// mirrors the API's parseLoRaJSON validation (api/src/normalize.ts):
//
//   - require both latitude and longitude fields to be present
//   - reject exact 0,0 (the "no fix" marker)
//   - tolerate the compact integer-scaled format (e.g. 422949 = 42.2949°N)
//
// When in doubt, the helper should return true — the uploader records any
// server-side rejection in the failed table, so the worst case of a false
// positive is one wasted API call.
func TestHasUsablePosition(t *testing.T) {
	cases := []struct {
		name string
		in   map[string]interface{}
		want bool
	}{
		{
			name: "standard fix",
			in:   map[string]interface{}{"lat": 42.2949, "lon": -83.711},
			want: true,
		},
		{
			name: "alias latitude/longitude",
			in:   map[string]interface{}{"latitude": 42.0, "longitude": -83.0},
			want: true,
		},
		{
			name: "compact integer scaled",
			in:   map[string]interface{}{"lat": float64(422949), "lon": float64(-837110)},
			want: true,
		},
		{
			name: "strings with numeric content",
			in:   map[string]interface{}{"lat": "42.2949", "lon": "-83.711"},
			want: true,
		},
		{
			name: "exact 0,0 rejected",
			in:   map[string]interface{}{"lat": 0.0, "lon": 0.0},
			want: false,
		},
		{
			name: "missing lat",
			in:   map[string]interface{}{"lon": -83.711},
			want: false,
		},
		{
			name: "missing lon",
			in:   map[string]interface{}{"lat": 42.2949},
			want: false,
		},
		{
			name: "missing both",
			in:   map[string]interface{}{"call": "TESTPL", "alt": 1234},
			want: false,
		},
		{
			name: "lat zero but lon nonzero — tolerated",
			in:   map[string]interface{}{"lat": 0.0, "lon": -83.711},
			want: true,
		},
		{
			name: "lat nonzero but lon zero — tolerated",
			in:   map[string]interface{}{"lat": 42.2949, "lon": 0.0},
			want: true,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := hasUsablePosition(tc.in); got != tc.want {
				t.Errorf("hasUsablePosition(%+v) = %v, want %v", tc.in, got, tc.want)
			}
		})
	}
}
