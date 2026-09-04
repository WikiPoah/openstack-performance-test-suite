Feature: Platform discovery

  Scenario: A platform consumer can discover required services and a usable boot image
    Given a configured writable OpenStack consumer environment
    When the consumer discovers required services and the expected boot image
    Then the required services and boot image should be available
