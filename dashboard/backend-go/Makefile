.PHONY: build run clean test

build:
	go build -o relay .

run: build
	./relay

clean:
	rm -f relay

test:
	go test ./...

docker-build:
	docker build -t umich-balloons-relay .

docker-run:
	docker run --rm -p 8080:8080 --env-file .env umich-balloons-relay
