.PHONY: run test benchmark docker-build docker-run

run:
	python3 httpd.py --host 127.0.0.1 --port 8080 --document-root www

test:
	python3 -m unittest discover -s tests -v

benchmark:
	ab -n 10000 -c 100 http://127.0.0.1:8080/

docker-build:
	docker build -t otus-httpd .

docker-run:
	docker run --rm -p 8080:80 \
		-v "$(PWD)/httpd.conf.example:/etc/httpd.conf:ro" \
		-v "$(PWD)/www:/var/www/html:ro" \
		otus-httpd
