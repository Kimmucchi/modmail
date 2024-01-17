FROM python:3.10 as py

FROM py as build

RUN apt update && apt install -y g++ git

COPY requirements.txt /
RUN pip install --prefix=/inst -U -r /requirements.txt

FROM py

COPY --from=build /inst /usr/local

ENV USING_DOCKER yes
RUN useradd --system --no-create-home modmail
USER modmail

WORKDIR /modmailbot
CMD ["python", "bot.py"]
COPY --chown=modmail:modmail . /modmailbot

FROM ghcr.io/modmail-dev/modmail@sha256:b014674c9f141a73e584b546a2ae19eb384732b1fb786639820faea2b455a7fa
RUN pipenv install
