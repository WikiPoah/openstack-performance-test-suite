Feature: Virtual machine lifecycle

  Scenario: A platform consumer can provision and remove a virtual machine
    Given a configured OpenStack test environment
    And a usable image, flavor, and network
    When the consumer runs the virtual machine lifecycle
    Then the lifecycle should succeed
