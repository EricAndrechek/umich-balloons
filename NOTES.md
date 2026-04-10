# Notes

Eric's notes while working on this project.

## SondeHub Integration

- TODO: technically supposed to use different callsign for each modulation type per balloon... [(github wiki)](https://github.com/projecthorus/sondehub-amateur-tracker/wiki/Getting-your-Flight-on-the-Sondehub-Amateur-Tracker#a-note-on-callsigns) - sucks for UI though...
- [Upload format](https://github.com/projecthorus/sondehub-infra/wiki/%5BDRAFT%5D-Amateur-Balloon-Telemetry-Format)
- [API Swagger Docs](https://generator.swagger.io/?url=https://raw.githubusercontent.com/projecthorus/sondehub-infra/main/swagger.yaml) or [GitHub Wiki](https://github.com/projecthorus/sondehub-infra/wiki/API-(Beta)#amateurtelemetry)
- [SondeHub's Amateur telemetry PUT logs](https://grafana.v2.sondehub.org/d/QsaLO65Vk/logs?orgId=1&from=now-24h&to=now&timezone=browser&viewPanel=panel-4) (are yet to show useful failures for me, mine never show...?)

## API Relay Server

- [Data format iridium sends in](https://docs.groundcontrol.com/iot/rockblock/web-services/receiving-mo-message)
- As per [SondeHub's API Response Codes Doc](https://github.com/projecthorus/sondehub-infra/wiki/API-(Beta)#notes-on-api-response-codes), only `200` is data submitted ok. `20x` means submitted but some issues (in response), `40x` is data is bad (see response), and `50x` is server error, retry up to 5 times. Anything other than a `200` or `50x` should be logged/displayed easily for us to debug...
- 

## Ground Stations

- [aprs-pigate](https://github.com/w2bro/aprs-pigate)
- Packer builder: [mkaczanowski/packer-builder-arm](https://github.com/mkaczanowski/packer-builder-arm) or [solo-io/packer-plugin-arm-image](https://github.com/solo-io/packer-plugin-arm-image) (currently using the former, not sure if one is better than other?)
- Flashing done via Raspberry Pi Imager (need to add to docs instead of `dd`), potentially also worth looking at [hypriot/flash](https://github.com/hypriot/flash) for a more automated approach? Was using SDM for build + flash but very slow and difficult to use.
- Appears we could add `SHUB` or `SHUB1-1` to APRS path in chase car position reports to have them also show up on SondeHub? See [here](https://github.com/projecthorus/sondehub-aprs-gateway?tab=readme-ov-file#chase-car-positions) for more details.
- See data usage (over time??) and to where?
- Cell ID mapping backup?? Use cell tower ID from hologram (like `181773527`) to [CellMapper's eNB ID Calculator](https://www.cellmapper.net/enbid?net=LTE&cellid=181853200) (or do the math manually, just the first 20 bits), get eNB ID (like `710364`), then select carrier and lookup tower with that ID on [CellMapper](https://www.cellmapper.net/map?MCC=310&MNC=410&type=LTE&latitude=42.28797302834212&longitude=-83.80995833210255&zoom=16.186666666666664&showTowers=true&showIcons=true&showTowerLabels=true&clusterEnabled=true&tilesEnabled=true&showOrphans=false&showNoFrequencyOnly=false&showFrequencyOnly=false&showBandwidthOnly=false&DateFilterType=Last&showHex=false&showVerifiedOnly=false&showUnverifiedOnly=false&showLTECAOnly=false&showENDCOnly=false&showBand=0&showSectorColours=true&mapType=roadmap&darkMode=false&imperialUnits=false)

## Umich-Balloons.com Webpage

- Should link to:
    - [predict](https://predict.sondehub.org/) resource
    - [balloon burst calc](https://sondehub.org/calc/)
    - [Grafana dashboards](https://grafana.v2.sondehub.org/dashboards) but which one(s)?
    - [SondeHub (Amateur)](https://amateur.sondehub.org/#!mt=Mapnik&mz=12&qm=12h&mc=42.28274,-83.66309&f=KF8ABL-13) page ideally for each balloon/car?
- Maybe use tawhiri api for predictions embedded? (https://api.v2.sondehub.org/tawhiri)
- Maybe make live server logs visible on the webpage for easier debugging?
- [SondeHub MQTT/WS](https://github.com/projecthorus/sondehub-infra/wiki/MQTT-Websockets)